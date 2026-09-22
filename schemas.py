from datetime import datetime
from pydantic import BaseModel, Field


class TelemetryPayload(BaseModel):
    """
    A single telemetry reading sent by a vehicle's IoT dongle
    every ~10 seconds, per the dossier's Edge/Vehicle layer.
    """

    vin: str = Field(..., description="Unique vehicle identifier")
    timestamp: datetime = Field(..., description="Time the reading was captured on the device")

    # Battery / BMS data
    battery_voltage: float = Field(..., gt=0, description="BMS voltage reading (V)")
    state_of_charge: float = Field(..., ge=0, le=100, description="Battery SOC as a percentage")
    kwh_consumed: float = Field(..., ge=0, description="Energy consumed since last reading (kWh)")

    # GPS data
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    speed_kmh: float = Field(..., ge=0, description="Vehicle speed in km/h")
