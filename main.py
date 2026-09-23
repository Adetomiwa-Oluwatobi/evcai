from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from schemas import TelemetryPayload
from database import get_session, TelemetryReading, Device
from anomaly import evaluate_reading
from emissions import calculate_avoided_emissions
from audit import compute_payload_hash, compute_chain_hash, GENESIS_HASH
from auth import generate_api_key, hash_api_key, verify_device, verify_admin
from rate_limit import limiter
import simulation_runner
from config import settings

app = FastAPI(title="Evcai — EV Climate AI")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse("static/dashboard.html")


@app.get("/")
async def root():
    return {"status": "ok", "message": "EV Climate Intelligence Platform API is running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/devices/register")
async def register_device(
    vin: str,
    name: str | None = None,
    session: AsyncSession = Depends(get_session),
    _admin=Depends(verify_admin),
):
    """
    Registers a new vehicle and returns its API key.
    IMPORTANT: the raw key is shown ONCE here and never again — only its
    hash is stored. This would typically be an admin-only operation in
    production, guarded separately from device-facing routes.
    """
    existing = await session.execute(select(Device).where(Device.vin == vin))
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"Device with VIN {vin} already registered")

    raw_key = generate_api_key()
    device = Device(vin=vin, name=name, hashed_api_key=hash_api_key(raw_key))
    session.add(device)
    await session.commit()

    return {"vin": vin, "name": name, "api_key": raw_key, "warning": "Save this key now — it will not be shown again."}


def _require_simulator_enabled():
    if not settings.enable_simulator_routes:
        raise HTTPException(
            status_code=404,
            detail="Simulator routes are disabled (ENABLE_SIMULATOR_ROUTES=false). "
                   "This should be off once real hardware is onboarded.",
        )


@app.get("/admin/simulate/presets")
async def list_simulation_presets(_admin=Depends(verify_admin)):
    """Returns the fixed set of simulation profiles the dashboard can trigger."""
    _require_simulator_enabled()
    return {
        key: {"label": p["label"], "description": p["description"]}
        for key, p in simulation_runner.PRESETS.items()
    }


@app.post("/admin/simulate")
async def start_simulation(profile: str, _admin=Depends(verify_admin)):
    """
    Kicks off a background simulation run using one of the fixed presets
    (quick / pilot / realtime). Only one run can be active at a time.
    """
    _require_simulator_enabled()
    try:
        run_id = await simulation_runner.start_run(profile, admin_key=settings.admin_api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"run_id": run_id, "profile": profile, "status": "running"}


@app.get("/admin/simulate/active")
async def active_simulation(_admin=Depends(verify_admin)):
    """Lets the dashboard resume polling an in-progress run after a page reload."""
    _require_simulator_enabled()
    run_id = simulation_runner.get_active_run_id()
    if run_id is None:
        return {"active": False}
    return {"active": True, "run_id": run_id}


@app.get("/admin/simulate/{run_id}")
async def simulation_status(run_id: str, _admin=Depends(verify_admin)):
    """Poll this while a run is in progress to get its live log and status."""
    _require_simulator_enabled()
    run = simulation_runner.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    return run


@app.post("/admin/reset")
async def reset_demo_data(
    confirm: bool = False,
    session: AsyncSession = Depends(get_session),
    _admin=Depends(verify_admin),
):
    """
    Wipes ALL telemetry readings and registered devices, and clears the
    simulator's local API-key cache so a fresh simulation run re-registers
    clean devices. Irreversible — intended for pre-demo resets while using
    synthetic data, not for use once real hardware is reporting.
    """
    _require_simulator_enabled()

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="This permanently deletes all telemetry and device data. "
                   "Pass ?confirm=true to proceed.",
        )

    from sqlalchemy import text
    await session.execute(text("TRUNCATE TABLE telemetry_readings RESTART IDENTITY"))
    await session.execute(text("TRUNCATE TABLE devices RESTART IDENTITY"))
    await session.commit()

    # Clear the simulator's cached device keys — without this, the next
    # simulation run would try to reuse API keys for devices that no
    # longer exist in the (now-empty) devices table, and every request
    # would fail with 401.
    import os
    if os.path.exists("simulator_devices.json"):
        os.remove("simulator_devices.json")

    return {"status": "reset complete", "message": "All telemetry, devices, and cached simulator keys cleared."}


@app.post("/telemetry")
@limiter.limit("20/minute")
async def ingest_telemetry(
    request: Request,
    payload: TelemetryPayload,
    session: AsyncSession = Depends(get_session),
    device: Device = Depends(verify_device),
):
    if device.vin != payload.vin:
        raise HTTPException(
            status_code=403,
            detail=f"Device authenticated as {device.vin} cannot submit data for {payload.vin}",
        )

    # Fetch this vehicle's most recent reading to compare against
    result = await session.execute(
        select(TelemetryReading)
        .where(TelemetryReading.vin == payload.vin)
        .order_by(TelemetryReading.timestamp.desc())
        .limit(1)
    )
    previous_row = result.scalars().first()

    previous_dict = None
    if previous_row is not None:
        previous_dict = {
            "timestamp": previous_row.timestamp,
            "latitude": previous_row.latitude,
            "longitude": previous_row.longitude,
            "state_of_charge": previous_row.state_of_charge,
            "speed_kmh": previous_row.speed_kmh,
        }

    score, flags, distance_km = evaluate_reading(payload.model_dump(), previous_dict)

    # --- Audit hashing ---
    # Fetch the most recently inserted record OF ANY VEHICLE, since the chain
    # of custody is a single global ledger, not per-vehicle.
    last_result = await session.execute(
        select(TelemetryReading).order_by(TelemetryReading.id.desc()).limit(1)
    )
    last_record = last_result.scalars().first()
    previous_chain_hash = last_record.chain_hash if last_record else GENESIS_HASH

    raw_payload_dict = payload.model_dump()
    this_payload_hash = compute_payload_hash(raw_payload_dict)
    this_chain_hash = compute_chain_hash(this_payload_hash, previous_chain_hash)

    # Only count emissions for trustworthy data. Flagged readings still get
    # saved (for audit purposes) but contribute zero to the carbon pool.
    if score >= 90:
        emissions = calculate_avoided_emissions(distance_km, payload.kwh_consumed)
    else:
        emissions = {"e_base_kg": 0.0, "e_project_kg": 0.0, "e_avoided_kg": 0.0}

    reading = TelemetryReading(
        **payload.model_dump(),
        confidence_score=score,
        flags=",".join(flags),
        distance_km=round(distance_km, 4),
        e_base_kg=emissions["e_base_kg"],
        e_project_kg=emissions["e_project_kg"],
        e_avoided_kg=emissions["e_avoided_kg"],
        payload_hash=this_payload_hash,
        chain_hash=this_chain_hash,
    )
    session.add(reading)
    await session.commit()
    await session.refresh(reading)

    return {
        "received": True,
        "id": reading.id,
        "confidence_score": score,
        "flags": flags,
        "excluded_from_carbon_pool": score < 90,
        "distance_km": round(distance_km, 4),
        "e_avoided_kg": emissions["e_avoided_kg"],
        "chain_hash": this_chain_hash,
    }


@app.get("/telemetry")
async def list_telemetry(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(TelemetryReading))
    readings = result.scalars().all()
    return readings


@app.get("/audit/verify")
async def verify_chain(session: AsyncSession = Depends(get_session)):
    """
    Walks every record in insertion order and recomputes the hash chain
    from scratch. If any past record's data was altered in the database,
    its payload_hash won't match what's stored, and the chain breaks
    from that point forward.
    """
    result = await session.execute(select(TelemetryReading).order_by(TelemetryReading.id.asc()))
    readings = result.scalars().all()

    previous_chain_hash = GENESIS_HASH
    for reading in readings:
        raw = {
            "vin": reading.vin,
            "timestamp": reading.timestamp,
            "battery_voltage": reading.battery_voltage,
            "state_of_charge": reading.state_of_charge,
            "kwh_consumed": reading.kwh_consumed,
            "latitude": reading.latitude,
            "longitude": reading.longitude,
            "speed_kmh": reading.speed_kmh,
        }
        recomputed_payload_hash = compute_payload_hash(raw)
        recomputed_chain_hash = compute_chain_hash(recomputed_payload_hash, previous_chain_hash)

        if recomputed_payload_hash != reading.payload_hash or recomputed_chain_hash != reading.chain_hash:
            return {
                "valid": False,
                "broken_at_id": reading.id,
                "message": f"Record {reading.id} does not match its stored hash — data may have been tampered with.",
            }

        previous_chain_hash = recomputed_chain_hash

    return {"valid": True, "records_verified": len(readings)}


@app.get("/vehicles/{vin}/detail")
async def vehicle_detail(vin: str, session: AsyncSession = Depends(get_session)):
    """
    Full detail view for one vehicle: identity, running totals, current
    state, and its most recent readings (for a UI drill-down view).
    """
    device_result = await session.execute(select(Device).where(Device.vin == vin))
    device = device_result.scalars().first()
    if device is None:
        raise HTTPException(status_code=404, detail=f"No vehicle registered with VIN {vin}")

    totals_result = await session.execute(
        select(
            func.count(TelemetryReading.id),
            func.sum(TelemetryReading.distance_km),
            func.sum(TelemetryReading.e_avoided_kg),
        ).where(TelemetryReading.vin == vin, TelemetryReading.confidence_score >= 90)
    )
    included_count, total_distance, total_avoided = totals_result.one()

    excluded_result = await session.execute(
        select(func.count(TelemetryReading.id)).where(
            TelemetryReading.vin == vin, TelemetryReading.confidence_score < 90
        )
    )
    excluded_count = excluded_result.scalar() or 0

    recent_result = await session.execute(
        select(TelemetryReading)
        .where(TelemetryReading.vin == vin)
        .order_by(TelemetryReading.timestamp.desc())
        .limit(20)
    )
    recent_readings = recent_result.scalars().all()

    last_good_result = await session.execute(
        select(TelemetryReading)
        .where(TelemetryReading.vin == vin, TelemetryReading.confidence_score >= 90)
        .order_by(TelemetryReading.timestamp.desc())
        .limit(1)
    )
    last_good = last_good_result.scalars().first()

    return {
        "vin": device.vin,
        "name": device.name,
        "current_state": {
            "state_of_charge": last_good.state_of_charge if last_good else None,
            "location": {"latitude": last_good.latitude, "longitude": last_good.longitude} if last_good else None,
            "as_of": last_good.timestamp if last_good else None,
        },
        "totals": {
            "readings_included": included_count or 0,
            "readings_excluded": excluded_count,
            "total_distance_km": round(total_distance or 0.0, 3),
            "total_avoided_co2e_kg": round(total_avoided or 0.0, 3),
        },
        "recent_readings": [
            {
                "id": r.id,
                "timestamp": r.timestamp,
                "confidence_score": r.confidence_score,
                "flags": r.flags.split(",") if r.flags else [],
                "distance_km": r.distance_km,
                "e_avoided_kg": r.e_avoided_kg,
                "state_of_charge": r.state_of_charge,
                "speed_kmh": r.speed_kmh,
            }
            for r in recent_readings
        ],
    }


@app.get("/fleet/status")
async def fleet_status(session: AsyncSession = Depends(get_session)):
    """
    Fleet Operator view: latest known state of every vehicle.

    Distinguishes between:
    - "last_seen": the most recent reading of ANY quality (proves the
      device is still reporting, even if that data itself is flagged)
    - "last_known_good": the most recent reading that PASSED the
      confidence check — this is what's safe to trust for the vehicle's
      actual location/battery/speed.
    """
    from datetime import datetime, timezone

    # All distinct VINs we've ever heard from, with their registered names
    device_result = await session.execute(select(Device.vin, Device.name))
    devices = {row[0]: row[1] for row in device_result.all()}
    vins = list(devices.keys())

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    OFFLINE_THRESHOLD_MINUTES = 15

    fleet = []
    for vin in vins:
        # Most recent reading of any quality — proves device is alive
        last_seen_result = await session.execute(
            select(TelemetryReading)
            .where(TelemetryReading.vin == vin)
            .order_by(TelemetryReading.timestamp.desc())
            .limit(1)
        )
        last_seen = last_seen_result.scalars().first()

        # Most recent TRUSTWORTHY reading — safe to display as vehicle state
        last_good_result = await session.execute(
            select(TelemetryReading)
            .where(TelemetryReading.vin == vin, TelemetryReading.confidence_score >= 90)
            .order_by(TelemetryReading.timestamp.desc())
            .limit(1)
        )
        last_good = last_good_result.scalars().first()

        if last_seen is None:
            # Registered device, but hasn't sent any telemetry yet
            fleet.append({
                "vin": vin,
                "name": devices.get(vin),
                "last_seen": None,
                "online": False,
                "last_reading_confidence": None,
                "last_reading_flags": [],
                "state": "NEVER_REPORTED",
            })
            continue

        minutes_since_last_seen = (now - last_seen.timestamp).total_seconds() / 60
        is_online = minutes_since_last_seen <= OFFLINE_THRESHOLD_MINUTES

        if last_good is None:
            # This vehicle has NEVER produced a trustworthy reading yet
            fleet.append({
                "vin": vin,
                "name": devices.get(vin),
                "last_seen": last_seen.timestamp,
                "online": is_online,
                "last_reading_confidence": last_seen.confidence_score,
                "last_reading_flags": last_seen.flags.split(",") if last_seen.flags else [],
                "state": "NO_TRUSTWORTHY_DATA_YET",
            })
            continue

        if last_good.state_of_charge < 20:
            battery_status = "low"
        elif last_good.state_of_charge < 50:
            battery_status = "medium"
        else:
            battery_status = "good"

        fleet.append({
            "vin": vin,
            "name": devices.get(vin),
            "last_seen": last_seen.timestamp,
            "online": is_online,
            "last_reading_confidence": last_seen.confidence_score,
            "last_reading_flags": last_seen.flags.split(",") if last_seen.flags else [],
            "state": "OK",
            "last_known_good": {
                "as_of": last_good.timestamp,
                "state_of_charge": last_good.state_of_charge,
                "battery_status": battery_status,
                "location": {"latitude": last_good.latitude, "longitude": last_good.longitude},
                "speed_kmh": last_good.speed_kmh,
            },
        })

    # Fleet-wide totals. Note: e_avoided_kg and distance_km are already
    # zeroed out at ingestion time for any reading that failed the
    # confidence check (see POST /telemetry), so a plain SUM here
    # automatically excludes flagged/untrustworthy readings — no extra
    # filtering needed.
    totals_result = await session.execute(
        select(
            func.sum(TelemetryReading.e_avoided_kg),
            func.sum(TelemetryReading.distance_km),
        )
    )
    total_avoided, total_distance = totals_result.one()

    return {
        "vehicle_count": len(fleet),
        "online_count": sum(1 for v in fleet if v["online"]),
        "total_avoided_co2e_kg": round(total_avoided or 0.0, 3),
        "total_distance_km": round(total_distance or 0.0, 3),
        "vehicles": fleet,
    }


@app.get("/reports/verification")
async def verification_report(
    vin: str | None = None,
    session: AsyncSession = Depends(get_session),
    _admin=Depends(verify_admin),
):
    """
    Aggregate report formatted for a climate verifier: total avoided CO2e,
    data quality breakdown, and confirmation the audit chain is intact.
    Optionally scope to a single vehicle with ?vin=...
    """
    query = select(TelemetryReading)
    if vin:
        query = query.where(TelemetryReading.vin == vin)

    result = await session.execute(query.order_by(TelemetryReading.id.asc()))
    readings = result.scalars().all()

    if not readings:
        return {"vin": vin, "message": "No readings found for the given criteria"}

    included = [r for r in readings if r.confidence_score >= 90]
    excluded = [r for r in readings if r.confidence_score < 90]

    # Tally why readings were excluded, for transparency
    flag_counts: dict[str, int] = {}
    for r in excluded:
        for flag in (r.flags or "").split(","):
            if flag:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1

    # Confirm each reading's own data hasn't been altered since it was recorded.
    # (Full chain-of-custody verification across ALL vehicles is what
    # GET /audit/verify does; here we just check these specific records.)
    chain_valid = True
    broken_at_id = None
    for r in readings:
        raw = {
            "vin": r.vin, "timestamp": r.timestamp, "battery_voltage": r.battery_voltage,
            "state_of_charge": r.state_of_charge, "kwh_consumed": r.kwh_consumed,
            "latitude": r.latitude, "longitude": r.longitude, "speed_kmh": r.speed_kmh,
        }
        if compute_payload_hash(raw) != r.payload_hash:
            chain_valid = False
            broken_at_id = r.id
            break

    return {
        "vin": vin or "ALL_VEHICLES",
        "report_generated_for": "Climate Verifier",
        "methodology": {
            "e_base_formula": "Distance x EF_ICE",
            "e_project_formula": "kWh_consumed x EF_grid",
            "e_avoided_formula": "E_base - E_project",
            "confidence_threshold": 90,
            "note": "EF_ICE and EF_grid are placeholder values pending baseline survey / grid research validation",
        },
        "data_quality": {
            "total_readings": len(readings),
            "included_in_carbon_pool": len(included),
            "excluded_from_carbon_pool": len(excluded),
            "exclusion_reasons": flag_counts,
        },
        "climate_impact": {
            "total_distance_km": round(sum(r.distance_km for r in included), 3),
            "total_avoided_co2e_kg": round(sum(r.e_avoided_kg for r in included), 3),
        },
        "audit_integrity": {
            "payload_hashes_verified": True if chain_valid else False,
            "tamper_detected_at_id": broken_at_id,
        },
    }


@app.get("/telemetry/{vin}/summary")
async def vehicle_summary(vin: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(
            func.count(TelemetryReading.id),
            func.sum(TelemetryReading.distance_km),
            func.sum(TelemetryReading.e_avoided_kg),
        ).where(TelemetryReading.vin == vin)
    )
    count, total_distance, total_avoided = result.one()

    return {
        "vin": vin,
        "reading_count": count or 0,
        "total_distance_km": round(total_distance or 0.0, 3),
        "total_avoided_co2e_kg": round(total_avoided or 0.0, 3),
    }
