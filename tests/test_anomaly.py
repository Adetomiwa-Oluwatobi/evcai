from datetime import datetime, timedelta
from anomaly import evaluate_reading, haversine_km


def make_reading(lat, lon, soc, speed, timestamp):
    return {
        "latitude": lat, "longitude": lon,
        "state_of_charge": soc, "speed_kmh": speed,
        "timestamp": timestamp,
    }


def test_first_reading_for_vehicle_has_full_confidence():
    current = make_reading(9.0, 7.0, 80, 20, datetime(2026, 1, 1, 10, 0, 0))
    score, flags, distance = evaluate_reading(current, previous=None)

    assert score == 100.0
    assert flags == []
    assert distance == 0.0


def test_normal_short_hop_is_not_flagged():
    previous = make_reading(9.0765, 7.3986, 76.5, 34.2, datetime(2026, 1, 1, 10, 0, 0))
    current = make_reading(9.0767, 7.3988, 76.3, 36.0, datetime(2026, 1, 1, 10, 0, 10))

    score, flags, distance = evaluate_reading(current, previous)

    assert score == 100.0
    assert flags == []
    assert distance < 0.1  # a few meters, not flagged


def test_gps_jump_is_flagged_and_penalized():
    previous = make_reading(9.0, 7.0, 76.0, 30.0, datetime(2026, 1, 1, 10, 0, 0))
    # 50km away, 10 seconds later => impossible speed
    current = make_reading(9.5, 7.8, 75.0, 30.0, datetime(2026, 1, 1, 10, 0, 10))

    score, flags, distance = evaluate_reading(current, previous)

    assert "gps_jump" in flags
    assert score < 90  # should be excluded from carbon pool


def test_soc_increase_while_moving_is_flagged():
    previous = make_reading(9.0, 7.0, 70.0, 30.0, datetime(2026, 1, 1, 10, 0, 0))
    current = make_reading(9.0001, 7.0001, 80.0, 40.0, datetime(2026, 1, 1, 10, 0, 10))

    score, flags, distance = evaluate_reading(current, previous)

    assert "soc_increase_while_moving" in flags
    assert score < 100


def test_soc_increase_while_stationary_is_not_flagged():
    # Charging while parked is normal and should NOT be flagged
    previous = make_reading(9.0, 7.0, 70.0, 0.0, datetime(2026, 1, 1, 10, 0, 0))
    current = make_reading(9.0, 7.0, 80.0, 0.0, datetime(2026, 1, 1, 10, 5, 0))

    score, flags, distance = evaluate_reading(current, previous)

    assert "soc_increase_while_moving" not in flags
    assert score == 100.0


def test_non_increasing_timestamp_is_flagged():
    previous = make_reading(9.0, 7.0, 70.0, 20.0, datetime(2026, 1, 1, 10, 0, 10))
    current = make_reading(9.0, 7.0, 70.0, 20.0, datetime(2026, 1, 1, 10, 0, 0))  # earlier!

    score, flags, distance = evaluate_reading(current, previous)

    assert "non_increasing_timestamp" in flags
    assert score < 100


def test_haversine_known_distance():
    # Roughly the distance between two points ~1 degree of latitude apart (~111km)
    distance = haversine_km(0.0, 0.0, 1.0, 0.0)
    assert 110 < distance < 112
