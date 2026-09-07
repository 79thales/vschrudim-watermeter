"""Normalized VS Chrudim portal data."""
from dataclasses import dataclass, field
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
class DownloadMetadata:
    """Safe, structured details about one measured-state page retrieval."""

    source: str = "unknown"
    export_candidates_found: int = 0
    export_candidates_attempted: int = 0
    html_table_detected: bool = False


@dataclass(frozen=True, slots=True)
class WaterMeterData:
    place: ConsumptionPlace
    readings: tuple[MeterReading, ...]
    latest_consumption_m3: float | None
    missing_timestamps: tuple[datetime, ...] = ()
    recovery_attempts: int = 0
    download_metadata: DownloadMetadata = field(default_factory=DownloadMetadata)
