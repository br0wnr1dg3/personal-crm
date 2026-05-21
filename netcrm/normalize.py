"""Pure functions for normalizing company names into stable keys."""
from __future__ import annotations
import re

# Legal/corporate suffixes to strip (regex-escaped patterns, longest first)
_SUFFIX_PATTERNS = [
    r",?\s*inc\.?",
    r",?\s*incorporated",
    r",?\s*ltd\.?",
    r",?\s*limited",
    r",?\s*llc",
    r",?\s*l\.l\.c\.?",
    r",?\s*gmbh",
    r",?\s*plc",
    r",?\s*s\.?a\.?",
    r",?\s*s\.?r\.?l\.?",
    r",?\s*b\.?v\.?",
    r",?\s*co\.?",
    r",?\s*corp\.?",
    r",?\s*corporation",
    r",?\s*company",
    r",?\s*pty\.?",
]
_SUFFIX_RE = re.compile(
    r"(?:" + "|".join(_SUFFIX_PATTERNS) + r")\s*$",
    flags=re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[.,]+")


def company_key(raw: str | None) -> str:
    """Normalize a raw company name into a stable, comparable key."""
    if not raw:
        return ""
    s = raw.strip().lower()
    # & → " and " (spaces collapsed below)
    s = s.replace("&", " and ")
    # strip suffixes (run until stable; "Foo Inc Ltd" → "Foo")
    while True:
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    # strip residual punctuation, collapse whitespace
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s
