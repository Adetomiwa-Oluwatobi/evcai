# --- Emission factors ---
# These are placeholders. Per the dossier, both are marked "To Validate":
# - EF_ICE: baseline emissions of an equivalent petrol vehicle, from an ICE baseline survey
# - EF_grid: regional grid emission factor, varies by charging source (grid vs. diesel vs. solar)
#
# Values below are rough illustrative placeholders (kg CO2e) — replace with
# validated figures once the baseline survey / grid research is complete.

EF_ICE_KG_PER_KM = 0.12   # kg CO2e per km for an equivalent petrol tricycle/motorcycle
EF_GRID_KG_PER_KWH = 0.45  # kg CO2e per kWh of grid electricity used to charge the EV


def calculate_avoided_emissions(distance_km: float, kwh_consumed: float) -> dict:
    """
    Implements the dossier's core formula:
        E_base    = Distance x EF_ICE
        E_project = kWh_consumed x EF_grid
        E_avoided = E_base - E_project
    All values in kg CO2e.
    """
    e_base = distance_km * EF_ICE_KG_PER_KM
    e_project = kwh_consumed * EF_GRID_KG_PER_KWH
    e_avoided = e_base - e_project

    return {
        "e_base_kg": round(e_base, 4),
        "e_project_kg": round(e_project, 4),
        "e_avoided_kg": round(e_avoided, 4),
    }
