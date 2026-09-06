"""Normalized VS Chrudim portal data."""
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True, slots=True)
class ConsumptionPlace:
    evidence_number: str
    technical_number: str
    address: str
    contract: str
    accounting_version: str

    @property
    def identifier(self) -> str:
        return self.evidence_number or self.technical_number

@dataclass(frozen=True, slots=True)
class MeterReading:
    timestamp: datetime
    meter_state_m3: float
    meter: str = ""

@dataclass(frozen=True, slots=True)
class WaterMeterData:
    place: ConsumptionPlace
    readings: tuple[MeterReading, ...]
    latest_consumption_m3: float | None
    missing_timestamps: tuple[datetime, ...] = ()
    recovery_attempts: int = 0
