"""Thin HTTP client for Fiber AI's organization enrichment endpoint.

The mapping from JSON response → FiberEnrichment lives in `_to_enrichment`.
Adjust field names there to match Fiber's actual response shape; the public
`FiberClient.enrich(name)` interface should stay stable.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class FiberStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR = "error"
    PERMANENT_ERROR = "permanent_error"


@dataclass
class FiberEnrichment:
    status: FiberStatus
    units: int = 0
    industry: str | None = None
    sub_industry: str | None = None
    employee_band: str | None = None
    revenue_band: str | None = None
    funding_stage: str | None = None
    hq_country: str | None = None
    hq_region: str | None = None
    website: str | None = None
    description: str | None = None


class FiberClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def enrich(self, name: str) -> FiberEnrichment:
        try:
            r = self.client.post(
                f"{self.base_url}/v1/organizations/enrich",
                json={"name": name},
            )
        except httpx.HTTPError:
            return FiberEnrichment(status=FiberStatus.ERROR)

        units = int(r.headers.get("x-fiber-credits", "1") or "1")

        if r.status_code == 200:
            return self._to_enrichment(r.json(), units=units)
        if r.status_code == 404:
            return FiberEnrichment(status=FiberStatus.NOT_FOUND, units=units)
        if 400 <= r.status_code < 500:
            return FiberEnrichment(status=FiberStatus.PERMANENT_ERROR, units=units)
        return FiberEnrichment(status=FiberStatus.ERROR, units=units)

    @staticmethod
    def _to_enrichment(payload: dict[str, Any], units: int) -> FiberEnrichment:
        return FiberEnrichment(
            status=FiberStatus.OK,
            units=units,
            industry=payload.get("industry"),
            sub_industry=payload.get("sub_industry"),
            employee_band=payload.get("employee_band"),
            revenue_band=payload.get("revenue_band"),
            funding_stage=payload.get("funding_stage"),
            hq_country=payload.get("hq_country"),
            hq_region=payload.get("hq_region"),
            website=payload.get("website"),
            description=payload.get("description"),
        )

    def close(self) -> None:
        self.client.close()
