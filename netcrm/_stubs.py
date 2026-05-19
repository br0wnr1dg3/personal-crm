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


from netcrm.anthropic_client import ClassificationBatchResult


class StubClassifierClient:
    """Returns deterministic classifications keyed on words in raw_position."""
    def __init__(self):
        self.batches_called = 0

    def classify_batch(self, requests):
        self.batches_called += 1
        out = []
        for r in requests:
            pos = (r.raw_position or "").lower()
            role = "Other"
            sen = "Unknown"
            if "founder" in pos or "co-founder" in pos:
                role, sen = "Founder", "Founder"
            elif "sales" in pos or "account executive" in pos:
                role, sen = "Sales", "VP" if "vp" in pos else "IC"
            elif "marketing" in pos or "growth" in pos:
                role, sen = "Marketing", "Director" if "director" in pos else "IC"
            elif "engineer" in pos or "engineering" in pos:
                role, sen = "Engineering", "VP" if "vp" in pos else "IC"
                if "founding" in pos:
                    sen = "Founder"
            elif "product" in pos:
                role, sen = "Product", "C-suite" if "chief" in pos else "IC"
            elif "principal" in pos:
                role, sen = "Investor", "Director"
            elif "business development" in pos:
                role, sen = "BD", "Manager"
            elif "operations" in pos:
                role, sen = "Operations", "Director"
            elif "consultant" in pos:
                role, sen = "Consulting", "IC"
            elif "student" in pos:
                role, sen = "Student", "IC"
            out.append({"linkedin_url": r.linkedin_url, "role_bucket": role, "seniority": sen})
        return ClassificationBatchResult(out, input_tokens=600, output_tokens=200)
