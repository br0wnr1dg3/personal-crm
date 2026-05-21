import os

import httpx
import pytest
import respx

from netcrm.fiber import FiberClient, FiberStatus


@pytest.fixture
def fiber_client():
    client = FiberClient(api_key="test-key", base_url="https://api.fiber.test")
    yield client
    client.close()


@respx.mock
def test_enrich_returns_ok_on_match(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(200, json={
            "industry": "Software",
            "sub_industry": "SaaS",
            "employee_band": "51-200",
            "revenue_band": "$10M-$50M",
            "funding_stage": "Series B",
            "hq_country": "US",
            "hq_region": "California",
            "website": "https://example.com",
            "description": "A company.",
        })
    )
    result = fiber_client.enrich("Acme Inc")
    assert result.status == FiberStatus.OK
    assert result.industry == "Software"
    assert result.employee_band == "51-200"


@respx.mock
def test_enrich_returns_not_found_on_404(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = fiber_client.enrich("Nonexistent Co")
    assert result.status == FiberStatus.NOT_FOUND


@respx.mock
def test_enrich_returns_error_on_500(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    result = fiber_client.enrich("Anything")
    assert result.status == FiberStatus.ERROR


@respx.mock
def test_enrich_returns_permanent_error_on_400(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(400, json={"error": "malformed"})
    )
    result = fiber_client.enrich("???")
    assert result.status == FiberStatus.PERMANENT_ERROR


@respx.mock
def test_enrich_returns_error_on_network_failure(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = fiber_client.enrich("Acme")
    assert result.status == FiberStatus.ERROR
    assert result.units == 0


@respx.mock
def test_enrich_reports_units_consumed(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(200, headers={"x-fiber-credits": "2"}, json={
            "industry": "X"
        })
    )
    result = fiber_client.enrich("Acme")
    assert result.units == 2


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="set RUN_LIVE_TESTS=1 to hit real Fiber",
)
def test_live_enrich_well_known_company():
    """Sanity: real Fiber returns an industry for a well-known company.

    Costs one Fiber credit per run. Run manually before shipping changes that
    touch fiber.py to confirm the API contract still matches what we map.
    """
    api_key = os.environ.get("FIBER_API_KEY")
    base_url = os.environ.get("FIBER_API_BASE_URL", "https://api.fiberai.com")
    assert api_key, "FIBER_API_KEY required for RUN_LIVE_TESTS"
    client = FiberClient(api_key=api_key, base_url=base_url)
    try:
        result = client.enrich("Stripe")
        assert result.status == FiberStatus.OK
        assert result.industry  # non-empty
    finally:
        client.close()
