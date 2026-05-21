"""Batched Haiku classifier using tool-use JSON output."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

VALID_ROLE_BUCKETS = {
    "Sales", "BD", "Marketing", "Engineering", "Product", "Design",
    "Founder", "Investor", "Operations", "Consulting", "Student", "Other",
}
VALID_SENIORITY = {
    "IC", "Manager", "Director", "VP", "C-suite", "Founder", "Unknown",
}

TOOL_SCHEMA = {
    "name": "classify_people",
    "description": "Classify each person into a role bucket and seniority.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "linkedin_url": {"type": "string"},
                        "role_bucket": {
                            "type": "string",
                            "enum": sorted(VALID_ROLE_BUCKETS),
                        },
                        "seniority": {
                            "type": "string",
                            "enum": sorted(VALID_SENIORITY),
                        },
                    },
                    "required": ["linkedin_url", "role_bucket", "seniority"],
                },
            }
        },
        "required": ["classifications"],
    },
}

SYSTEM_PROMPT = """\
You classify LinkedIn connections into role_bucket and seniority based on the \
person's current job title and company. Return ONE classification per input \
person via the `classify_people` tool. Rules:

- role_bucket exactly one of: Sales, BD, Marketing, Engineering, Product, \
Design, Founder, Investor, Operations, Consulting, Student, Other.
- seniority exactly one of: IC, Manager, Director, VP, C-suite, Founder, Unknown.
- If the title contains 'Founder', 'Co-founder', or 'Owner', use role_bucket=Founder \
and seniority=Founder regardless of other words.
- 'Founding [X]' (e.g. Founding Engineer): keep the X-derived role_bucket but set \
seniority=Founder.
- 'Head of [X]': seniority=Director by default, VP for large companies. Use the \
company name as a hint when available.
- 'Chief X Officer' → seniority=C-suite.
- 'VP of X' / 'Vice President X' → seniority=VP.
- 'Director of X' → seniority=Director.
- 'Manager' / 'Lead' (without Director/VP/Chief) → seniority=Manager.
- Plain IC titles (Engineer, AE, SDR, Designer) → seniority=IC.
- Multi-titled people: use the FIRST title clause only.
- If you genuinely cannot tell: role_bucket=Other, seniority=Unknown.
"""


@dataclass
class ClassificationRequest:
    linkedin_url: str
    raw_position: str
    raw_company: str


@dataclass
class ClassificationBatchResult:
    classifications: list[dict[str, str]]
    input_tokens: int
    output_tokens: int


class ClassifierClient:
    def __init__(self, anthropic_client: Any, model: str):
        self.client = anthropic_client
        self.model = model

    def classify_batch(
        self, requests: list[ClassificationRequest],
    ) -> ClassificationBatchResult:
        """Classify a batch of people. Keep batch size <= 100 to stay within max_tokens=4096.

        At ~30 output tokens per classification, 4096 max_tokens supports ~136 items
        before Haiku truncates mid-JSON. We cap at 100 for headroom; the caller
        should chunk larger inputs.
        """
        if not requests:
            return ClassificationBatchResult([], 0, 0)
        if len(requests) > 100:
            raise ValueError(
                f"batch too large ({len(requests)} > 100); chunk and call repeatedly"
            )
        user_lines = [
            f"- url={r.linkedin_url} | position={r.raw_position!r} | "
            f"company={r.raw_company!r}"
            for r in requests
        ]
        user_msg = (
            "Classify each of the following people. Return one entry per "
            "url in the same order.\n\n" + "\n".join(user_lines)
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "classify_people"},
            messages=[{"role": "user", "content": user_msg}],
        )
        tool_block = next(
            (b for b in response.content
             if getattr(b, "type", "") == "tool_use"
             and getattr(b, "name", "") == "classify_people"),
            None,
        )
        raw_input = getattr(tool_block, "input", None) if tool_block else None
        raw = (raw_input.get("classifications", []) if isinstance(raw_input, dict) else [])
        cleaned: list[dict[str, str]] = []
        url_order = [r.linkedin_url for r in requests]
        by_url = {item.get("linkedin_url"): item for item in raw if isinstance(item, dict)}
        for url in url_order:
            item = by_url.get(url, {})
            role = item.get("role_bucket", "Other")
            sen = item.get("seniority", "Unknown")
            if role not in VALID_ROLE_BUCKETS:
                role = "Other"
            if sen not in VALID_SENIORITY:
                sen = "Unknown"
            cleaned.append({
                "linkedin_url": url,
                "role_bucket": role,
                "seniority": sen,
            })
        return ClassificationBatchResult(
            classifications=cleaned,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
