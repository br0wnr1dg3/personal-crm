from unittest.mock import MagicMock

import pytest

from netcrm.anthropic_client import ClassifierClient, ClassificationRequest


def _make_fake_anthropic(tool_input: list[dict], in_tok: int = 600, out_tok: int = 200):
    """Return a mock anthropic client returning a single tool_use block."""
    fake = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = "classify_people"
    block.input = {"classifications": tool_input}
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=in_tok, output_tokens=out_tok)
    fake.messages.create.return_value = response
    return fake


def test_classify_batch_parses_tool_use_output():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1", "role_bucket": "Sales", "seniority": "VP"},
        {"linkedin_url": "u2", "role_bucket": "Marketing", "seniority": "Director"},
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    requests = [
        ClassificationRequest(linkedin_url="u1", raw_position="VP Sales", raw_company="Acme"),
        ClassificationRequest(linkedin_url="u2", raw_position="Director Marketing", raw_company="Globex"),
    ]
    result = client.classify_batch(requests)
    assert result.classifications[0]["role_bucket"] == "Sales"
    assert result.input_tokens == 600
    assert result.output_tokens == 200


def test_classify_batch_defaults_missing_fields():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1"},   # missing role_bucket and seniority
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    requests = [ClassificationRequest(linkedin_url="u1",
                                      raw_position="???",
                                      raw_company="")]
    result = client.classify_batch(requests)
    assert result.classifications[0]["role_bucket"] == "Other"
    assert result.classifications[0]["seniority"] == "Unknown"


def test_classify_batch_clamps_invalid_enums():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1", "role_bucket": "WeirdValue", "seniority": "WhoKnows"},
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    result = client.classify_batch([ClassificationRequest(linkedin_url="u1",
                                                          raw_position="x",
                                                          raw_company="y")])
    assert result.classifications[0]["role_bucket"] == "Other"
    assert result.classifications[0]["seniority"] == "Unknown"


def test_classify_batch_handles_no_tool_use_block():
    """When Anthropic returns content with no tool_use block, default everything to Other/Unknown."""
    fake = MagicMock()
    response = MagicMock()
    response.content = []  # no tool_use block
    response.usage = MagicMock(input_tokens=100, output_tokens=0)
    fake.messages.create.return_value = response
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    result = client.classify_batch([
        ClassificationRequest(linkedin_url="u1", raw_position="Engineer", raw_company="Acme")
    ])
    assert result.classifications[0]["role_bucket"] == "Other"
    assert result.classifications[0]["seniority"] == "Unknown"
    assert result.input_tokens == 100


def test_classify_batch_rejects_oversize_batches():
    fake = MagicMock()
    client = ClassifierClient(fake, model="x")
    requests = [
        ClassificationRequest(linkedin_url=f"u{i}", raw_position="", raw_company="")
        for i in range(101)
    ]
    with pytest.raises(ValueError, match="batch too large"):
        client.classify_batch(requests)
