import math
from datetime import datetime


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two GPS points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def evaluate_reading(current: dict, previous: dict | None) -> tuple[float, list[str], float]:
    """
    Compares a new telemetry reading against the vehicle's previous reading.
    Returns (confidence_score 0-100, list of flag strings triggered, distance_km since last reading).
    """
    score = 100.0
    flags: list[str] = []

    if previous is None:
        # First-ever reading for this VIN — nothing to compare against yet, no distance traveled.
        return score, flags, 0.0

    time_delta_sec = (current["timestamp"] - previous["timestamp"]).total_seconds()

    # Guard against bad/duplicate timestamps
    if time_delta_sec <= 0:
        flags.append("non_increasing_timestamp")
        score -= 40
        return max(score, 0), flags, 0.0

    # --- GPS jump check ---
    distance_km = haversine_km(
        previous["latitude"], previous["longitude"],
        current["latitude"], current["longitude"],
    )
    implied_speed_kmh = distance_km / (time_delta_sec / 3600)

    MAX_PLAUSIBLE_SPEED_KMH = 120  # generous ceiling for an e-tricycle/motorcycle
    if implied_speed_kmh > MAX_PLAUSIBLE_SPEED_KMH:
        flags.append("gps_jump")
        score -= 50

    # --- Energy balance check ---
    # SOC should not rise while the vehicle is moving (no regen assumed for this vehicle class)
    soc_delta = current["state_of_charge"] - previous["state_of_charge"]
    if soc_delta > 0.5 and current["speed_kmh"] > 5:
        flags.append("soc_increase_while_moving")
        score -= 30

    return max(score, 0), flags, distance_km
