"""Cost tracking: per-call log, running total, spend cap, dry-run estimators."""
from __future__ import annotations
import math
import sqlite3
from datetime import datetime, timezone


class SpendCapExceeded(RuntimeError):
    pass


class CostTracker:
    def __init__(self, conn: sqlite3.Connection, max_spend_usd: float | None):
        self.conn = conn
        self.max_spend_usd = max_spend_usd
        self.spent_usd: float = 0.0

    def log(self, provider: str, operation: str, units: int,
            usd_cost: float, context: str | None = None) -> None:
        next_total = self.spent_usd + usd_cost
        if self.max_spend_usd is not None and next_total > self.max_spend_usd:
            raise SpendCapExceeded(
                f"spend cap ${self.max_spend_usd:.2f} would be exceeded "
                f"(current ${self.spent_usd:.2f} + ${usd_cost:.4f} = ${next_total:.2f})"
            )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO costs(ts, provider, operation, units, usd_cost, context)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.now(timezone.utc).isoformat(),
                 provider, operation, units, usd_cost, context),
            )
        self.spent_usd = next_total


def estimate_fiber(unenriched_count: int, usd_per_credit: float,
                   credits_per_call: int = 1) -> float:
    return unenriched_count * credits_per_call * usd_per_credit


def estimate_haiku(unclassified_count: int, batch_size: int,
                   input_tokens_per_call: int, output_tokens_per_call: int,
                   usd_per_input_mtok: float, usd_per_output_mtok: float) -> float:
    n_calls = math.ceil(unclassified_count / batch_size)
    per_call = (input_tokens_per_call * usd_per_input_mtok / 1_000_000 +
                output_tokens_per_call * usd_per_output_mtok / 1_000_000)
    return n_calls * per_call
