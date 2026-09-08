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
    # This is a fixed vocabulary of parser-recognized structures, never HTML,
    # URLs, field names or values. It lets diagnostics show a portal markup
    # change without disclosing customer data from the rendered page.
    portal_page_features: tuple[str, ...] = ()
    # Informational only. A meter replacement or corrected reading can make
    # the register decrease, and the existing statistics writer already
    # handles that case. These flags must never reject a valid download.
    reading_quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WaterMeterData:
    place: ConsumptionPlace
    readings: tuple[MeterReading, ...]
    latest_consumption_m3: float | None
    missing_timestamps: tuple[datetime, ...] = ()
    recovery_attempts: int = 0
    download_metadata: DownloadMetadata = field(default_factory=DownloadMetadata)
