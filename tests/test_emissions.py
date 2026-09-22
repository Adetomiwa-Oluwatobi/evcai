from emissions import calculate_avoided_emissions, EF_ICE_KG_PER_KM, EF_GRID_KG_PER_KWH


def test_zero_distance_zero_kwh_gives_zero_avoided():
    result = calculate_avoided_emissions(distance_km=0, kwh_consumed=0)
    assert result["e_base_kg"] == 0
    assert result["e_project_kg"] == 0
    assert result["e_avoided_kg"] == 0


def test_known_values_match_formula():
    distance = 10.0
    kwh = 1.0
    result = calculate_avoided_emissions(distance_km=distance, kwh_consumed=kwh)

    expected_base = distance * EF_ICE_KG_PER_KM
    expected_project = kwh * EF_GRID_KG_PER_KWH
    expected_avoided = expected_base - expected_project

    assert result["e_base_kg"] == round(expected_base, 4)
    assert result["e_project_kg"] == round(expected_project, 4)
    assert result["e_avoided_kg"] == round(expected_avoided, 4)


def test_avoided_can_go_negative_if_grid_is_dirty_enough():
    # If a vehicle drives almost no distance but somehow consumes a lot of
    # kWh (e.g. idling, or a data error), avoided emissions can go negative.
    # This is a real possible outcome, not a bug — the report layer should
    # surface it, not hide it.
    result = calculate_avoided_emissions(distance_km=0.01, kwh_consumed=5.0)
    assert result["e_avoided_kg"] < 0
