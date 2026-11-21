"""What one source returns after a soft-failing collect."""

from dataclasses import dataclass, field

from ..lead_store import StoredLead


@dataclass
class SourceOutcome:
    status: str
    detail: str = ""
    leads: list[StoredLead] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    enriched: int = 0

    def public(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "found": len(self.leads),
            "errors": list(self.errors),
            "enriched": self.enriched,
            "alerts": len(self.alerts),
        }
