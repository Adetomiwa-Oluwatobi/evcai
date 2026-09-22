"""
Synthetic telemetry simulator for the EV Climate Intelligence Platform.

Simulates one or more vehicles driving around, draining battery, and
reporting to the real POST /telemetry endpoint — standing in for actual
hardware until real dongles are selected and deployed.

Can be run two ways:
1. As a CLI script:       python simulator.py --vehicles 3 --readings 20
2. Imported and called from the API (see main.py's /admin/simulate routes),
   which is what powers the dashboard's "Simulation Control" panel.
"""

import argparse
import asyncio
import json
import os
import random
from datetime import datetime, timedelta
from typing import Callable

import httpx

DEVICE_KEY_STORE = "simulator_devices.json"  # local cache of vin -> api_key


def _default_api_base_url() -> str:
    # Matches whatever port THIS server process is actually bound to,
    # so it works both in local dev and inside a deployed container
    # where the platform assigns $PORT dynamically.
    return f"http://localhost:{os.environ.get('PORT', 8000)}"


def load_device_keys() -> dict:
    if os.path.exists(DEVICE_KEY_STORE):
        with open(DEVICE_KEY_STORE) as f:
            return json.load(f)
    return {}


def save_device_keys(keys: dict) -> None:
    with open(DEVICE_KEY_STORE, "w") as f:
        json.dump(keys, f, indent=2)


async def get_or_register_device(
    client: httpx.AsyncClient, api_base_url: str, vin: str, name: str,
    admin_key: str, known_keys: dict,
) -> str:
    """Reuses a cached API key if we've registered this VIN before, otherwise registers it fresh."""
    if vin in known_keys:
        return known_keys[vin]

    resp = await client.post(
        f"{api_base_url}/devices/register",
        params={"vin": vin, "name": name},
        headers={"X-Admin-Key": admin_key},
    )
    resp.raise_for_status()
    api_key = resp.json()["api_key"]
    known_keys[vin] = api_key
    save_device_keys(known_keys)
    return api_key


class SimulatedVehicle:
    """
    Tracks one vehicle's evolving state (position, battery) across
    successive readings, so each reading is a plausible continuation
    of the last — not just random noise.
    """

    def __init__(self, vin: str, start_lat: float, start_lon: float):
        self.vin = vin
        self.lat = start_lat
        self.lon = start_lon
        self.soc = random.uniform(70, 100)
        self.timestamp = datetime(2026, 9, 18, 8, 0, 0)

    def step(self, anomaly_rate: float) -> tuple[dict, str | None]:
        """
        Advances this vehicle by one reading. Returns (payload, injected_anomaly_type).
        injected_anomaly_type is None for a normal reading.
        """
        self.timestamp += timedelta(seconds=10)
        anomaly = None

        is_charging_stop = self.soc < 30 and random.random() < 0.3

        if is_charging_stop:
            speed = 0.0
            self.soc = min(100.0, self.soc + random.uniform(5, 15))
            kwh_consumed = 0.0
        else:
            speed = random.uniform(15, 40)
            distance_km = speed * (10 / 3600)
            self.lat += random.uniform(-0.0005, 0.0005) * (speed / 30)
            self.lon += random.uniform(-0.0005, 0.0005) * (speed / 30)
            kwh_consumed = round(distance_km * 0.09, 4)
            self.soc = max(0.0, self.soc - kwh_consumed * 2.5)

        lat, lon = self.lat, self.lon
        soc = self.soc

        if random.random() < anomaly_rate:
            anomaly_type = random.choice(["gps_jump", "soc_increase_while_moving"])
            if anomaly_type == "gps_jump":
                lat += random.uniform(0.3, 0.6)
                lon += random.uniform(0.3, 0.6)
                anomaly = "gps_jump"
            elif anomaly_type == "soc_increase_while_moving":
                soc = min(100.0, soc + 10)
                anomaly = "soc_increase_while_moving"

        payload = {
            "vin": self.vin,
            "timestamp": self.timestamp.isoformat(),
            "battery_voltage": round(48 + (soc / 100) * 12, 2),
            "state_of_charge": round(soc, 2),
            "kwh_consumed": kwh_consumed,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "speed_kmh": round(speed, 1),
        }
        return payload, anomaly


async def run_vehicle(
    client: httpx.AsyncClient, api_base_url: str, vin: str, api_key: str,
    num_readings: int, interval: float, anomaly_rate: float,
    log: Callable[[str], None],
):
    start_lat = 9.0765 + random.uniform(-0.05, 0.05)  # scattered around Abuja, Nigeria
    start_lon = 7.3986 + random.uniform(-0.05, 0.05)
    vehicle = SimulatedVehicle(vin, start_lat, start_lon)

    for i in range(num_readings):
        payload, injected_anomaly = vehicle.step(anomaly_rate)

        try:
            resp = await client.post(
                f"{api_base_url}/telemetry",
                json=payload,
                headers={"X-Device-Key": api_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                tag = f" [INJECTED: {injected_anomaly}]" if injected_anomaly else ""
                detected = f" -> detected: {body['flags']}" if body["flags"] else ""
                log(f"[{vin}] reading {i+1}/{num_readings} conf={body['confidence_score']} "
                    f"avoided={body['e_avoided_kg']}kg{tag}{detected}")
            elif resp.status_code == 429:
                log(f"[{vin}] reading {i+1}/{num_readings} RATE LIMITED — backing off")
                await asyncio.sleep(2)
            else:
                log(f"[{vin}] reading {i+1}/{num_readings} FAILED: {resp.status_code} {resp.text}")
        except httpx.ConnectError:
            log(f"[{vin}] Could not connect to API at {api_base_url} — is the server running?")
            return

        if interval > 0:
            await asyncio.sleep(interval)


async def run_simulation(
    vehicles: int, readings: int, interval: float, anomaly_rate: float,
    admin_key: str, api_base_url: str | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """
    Reusable entry point: registers (or reuses) N simulated vehicles and
    drives them through `readings` telemetry posts each. Used by both the
    CLI (`python simulator.py ...`) and the dashboard's Simulation Control
    panel (via main.py's /admin/simulate routes).
    """
    api_base_url = api_base_url or _default_api_base_url()
    known_keys = load_device_keys()

    async with httpx.AsyncClient(timeout=10.0) as client:
        vehicle_list = []
        for i in range(vehicles):
            vin = f"GEE774-SIM-{i+1:03d}"
            name = f"GEE774 Unit {i+1:03d}"
            api_key = await get_or_register_device(client, api_base_url, vin, name, admin_key, known_keys)
            vehicle_list.append((vin, api_key))

        log(f"Simulating {len(vehicle_list)} vehicle(s), {readings} readings each, "
            f"anomaly rate {anomaly_rate:.0%}")

        await asyncio.gather(*[
            run_vehicle(client, api_base_url, vin, key, readings, interval, anomaly_rate, log)
            for vin, key in vehicle_list
        ])

    log("Simulation complete.")


async def _cli_main():
    parser = argparse.ArgumentParser(description="Simulate EV fleet telemetry")
    parser.add_argument("--vehicles", type=int, default=3, help="Number of simulated vehicles")
    parser.add_argument("--readings", type=int, default=20, help="Readings per vehicle")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between readings (real-time pacing)")
    parser.add_argument("--fast", action="store_true", help="Ignore --interval, send as fast as possible")
    parser.add_argument("--anomaly-rate", type=float, default=0.05, help="Probability (0-1) of an anomalous reading")
    parser.add_argument("--admin-key", type=str, default=None, help="Admin key for device registration")
    args = parser.parse_args()

    admin_key = args.admin_key
    if admin_key is None:
        from config import settings
        admin_key = settings.admin_api_key

    interval = 0 if args.fast else args.interval

    await run_simulation(
        vehicles=args.vehicles, readings=args.readings, interval=interval,
        anomaly_rate=args.anomaly_rate, admin_key=admin_key, log=print,
    )


if __name__ == "__main__":
    asyncio.run(_cli_main())
