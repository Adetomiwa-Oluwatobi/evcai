import pytest


async def register_device(client, admin_key, vin="GEE774-TRK-TEST"):
    resp = await client.post(
        f"/devices/register?vin={vin}",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    return resp.json()["api_key"]


@pytest.mark.asyncio
async def test_register_device_requires_admin_key(client):
    resp = await client.post("/devices/register?vin=SOME-VIN")
    assert resp.status_code in (401, 422)  # missing header


@pytest.mark.asyncio
async def test_register_device_with_valid_admin_key(client, admin_key):
    resp = await client.post(
        "/devices/register?vin=GEE774-TRK-001",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vin"] == "GEE774-TRK-001"
    assert "api_key" in body


@pytest.mark.asyncio
async def test_telemetry_rejected_without_device_key(client):
    resp = await client.post(
        "/telemetry",
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.1,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 20.0,
        },
    )
    assert resp.status_code == 422  # missing required header


@pytest.mark.asyncio
async def test_telemetry_rejected_when_vin_mismatch(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    resp = await client.post(
        "/telemetry",
        headers={"X-Device-Key": device_key},
        json={
            "vin": "SOME-OTHER-VIN",  # claiming to be a different vehicle
            "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.1,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 20.0,
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_valid_telemetry_is_accepted_and_scored(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    resp = await client.post(
        "/telemetry",
        headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["confidence_score"] == 100.0
    assert body["excluded_from_carbon_pool"] is False
    assert "chain_hash" in body


@pytest.mark.asyncio
async def test_invalid_payload_rejected_by_schema(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    resp = await client.post(
        "/telemetry",
        headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0,
            "state_of_charge": 150.0,  # invalid: over 100%
            "kwh_consumed": 0.0, "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_gps_jump_excluded_from_emissions(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    # Teleporting 50km in 10 seconds
    resp = await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:10",
            "battery_voltage": 58.0, "state_of_charge": 79.0, "kwh_consumed": 0.02,
            "latitude": 9.5, "longitude": 7.8, "speed_kmh": 30.0,
        },
    )
    body = resp.json()
    assert "gps_jump" in body["flags"]
    assert body["excluded_from_carbon_pool"] is True
    assert body["e_avoided_kg"] == 0.0


@pytest.mark.asyncio
async def test_audit_chain_valid_after_normal_inserts(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    for i in range(3):
        await client.post(
            "/telemetry", headers={"X-Device-Key": device_key},
            json={
                "vin": "GEE774-TRK-001", "timestamp": f"2026-01-01T10:0{i}:00",
                "battery_voltage": 58.0, "state_of_charge": 80.0 - i, "kwh_consumed": 0.1,
                "latitude": 9.0 + i * 0.001, "longitude": 7.0, "speed_kmh": 20.0,
            },
        )

    resp = await client.get("/audit/verify")
    body = resp.json()
    assert body["valid"] is True
    assert body["records_verified"] == 3


@pytest.mark.asyncio
async def test_verification_report_excludes_flagged_readings(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    # One good reading
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    # One good follow-up (real distance covered)
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:05:00",
            "battery_voltage": 57.5, "state_of_charge": 75.0, "kwh_consumed": 0.3,
            "latitude": 9.02, "longitude": 7.0, "speed_kmh": 25.0,
        },
    )
    # One GPS-jump anomaly
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:05:10",
            "battery_voltage": 57.4, "state_of_charge": 74.0, "kwh_consumed": 0.02,
            "latitude": 9.5, "longitude": 7.8, "speed_kmh": 25.0,
        },
    )

    resp = await client.get(
        "/reports/verification?vin=GEE774-TRK-001",
        headers={"X-Admin-Key": admin_key},
    )
    body = resp.json()
    assert body["data_quality"]["total_readings"] == 3
    assert body["data_quality"]["excluded_from_carbon_pool"] == 1
    assert body["data_quality"]["exclusion_reasons"]["gps_jump"] == 1
    assert body["audit_integrity"]["payload_hashes_verified"] is True


@pytest.mark.asyncio
async def test_vehicle_detail_returns_name_and_history(client, admin_key):
    resp = await client.post(
        "/devices/register",
        params={"vin": "GEE774-TRK-001", "name": "Lagos Unit 1"},
        headers={"X-Admin-Key": admin_key},
    )
    device_key = resp.json()["api_key"]

    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )

    resp = await client.get("/vehicles/GEE774-TRK-001/detail")
    body = resp.json()

    assert body["vin"] == "GEE774-TRK-001"
    assert body["name"] == "Lagos Unit 1"
    assert body["totals"]["readings_included"] == 1
    assert len(body["recent_readings"]) == 1


@pytest.mark.asyncio
async def test_vehicle_detail_404_for_unknown_vin(client):
    resp = await client.get("/vehicles/DOES-NOT-EXIST/detail")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fleet_status_includes_totals_without_admin_key(client, admin_key):
    """
    Regression test: the dashboard's hero 'avoided emissions' card reads
    these fields directly from /fleet/status (no auth), not from the
    admin-gated verifier report. This locks in that the field exists,
    requires no auth, and matches the authoritative verifier total.
    """
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:05:00",
            "battery_voltage": 57.5, "state_of_charge": 75.0, "kwh_consumed": 0.3,
            "latitude": 9.02, "longitude": 7.0, "speed_kmh": 25.0,
        },
    )

    # No auth header at all — this must work for the dashboard's public view
    fleet_resp = await client.get("/fleet/status")
    fleet_body = fleet_resp.json()

    assert "total_avoided_co2e_kg" in fleet_body
    assert "total_distance_km" in fleet_body
    assert fleet_body["total_avoided_co2e_kg"] > 0

    verifier_resp = await client.get("/reports/verification", headers={"X-Admin-Key": admin_key})
    verifier_body = verifier_resp.json()

    assert fleet_body["total_avoided_co2e_kg"] == verifier_body["climate_impact"]["total_avoided_co2e_kg"]


@pytest.mark.asyncio
async def test_fleet_status_falls_back_to_last_known_good(client, admin_key):
    device_key = await register_device(client, admin_key, vin="GEE774-TRK-001")

    # Good reading
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:00",
            "battery_voltage": 58.0, "state_of_charge": 80.0, "kwh_consumed": 0.0,
            "latitude": 9.0, "longitude": 7.0, "speed_kmh": 0.0,
        },
    )
    # Anomalous follow-up (latest reading, but bad)
    await client.post(
        "/telemetry", headers={"X-Device-Key": device_key},
        json={
            "vin": "GEE774-TRK-001", "timestamp": "2026-01-01T10:00:10",
            "battery_voltage": 57.9, "state_of_charge": 79.0, "kwh_consumed": 0.02,
            "latitude": 9.5, "longitude": 7.8, "speed_kmh": 30.0,
        },
    )

    resp = await client.get("/fleet/status")
    body = resp.json()
    vehicle = body["vehicles"][0]

    # last_seen reflects the anomalous reading's timestamp...
    assert vehicle["last_reading_flags"] == ["gps_jump"]
    # ...but last_known_good still shows the earlier, trustworthy position
    assert vehicle["last_known_good"]["location"]["latitude"] == 9.0
