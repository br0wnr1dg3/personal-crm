"""In-memory stubs used by tests AND by the CLI when NETCRM_TEST_MODE=1.

Lives in the netcrm package (not under tests/) so the prod CLI can import it
without depending on pytest being installed.
"""
from __future__ import annotations
import json
from pathlib import Path

from netcrm.fiber import FiberEnrichment, FiberStatus
from netcrm.normalize import company_key


class StubFiberClient:
    """Returns canned Fiber responses keyed on normalized company_key.

    Companies not in the canned data return FiberStatus.NOT_FOUND.
    """
    def __init__(self, canned: dict[str, dict]):
        self.canned = canned
        self.calls: list[str] = []

    def enrich(self, name: str) -> FiberEnrichment:
        self.calls.append(name)
        key = company_key(name)
        if key in self.canned:
            return FiberEnrichment(status=FiberStatus.OK, units=1, **self.canned[key])
        return FiberEnrichment(status=FiberStatus.NOT_FOUND, units=1)


def load_canned_fiber_stub(fixtures_dir: Path) -> StubFiberClient:
    """Convenience: load the JSON fixture and return a stub."""
    canned = json.loads((fixtures_dir / "fiber_canned_responses.json").read_text())
    return StubFiberClient(canned)
