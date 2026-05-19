"""Classify-people stage: batch un-classified people through the Haiku tool."""
from __future__ import annotations
import sqlite3
from typing import Protocol

from netcrm.anthropic_client import (
    ClassificationBatchResult, ClassificationRequest,
)
from netcrm.cost import CostTracker
from netcrm.db import now_iso


class _ClassifierLike(Protocol):
    def classify_batch(
        self, requests: list[ClassificationRequest],
    ) -> ClassificationBatchResult: ...


def count_unclassified(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM people p
        LEFT JOIN people_class pc USING(linkedin_url)
        WHERE pc.linkedin_url IS NULL
        """
    ).fetchone()[0]


def classify_people(
    conn: sqlite3.Connection,
    classifier: _ClassifierLike,
    cost: CostTracker,
    model: str,
    batch_size: int,
    usd_per_input_mtok: float,
    usd_per_output_mtok: float,
) -> int:
    """Classify every un-classified person. Returns number of batches sent.

    All un-classified rows are fetched into memory before batching begins.
    Acceptable for v1 (max ~5,000 connections); switch to streaming
    fetchmany() if datasets grow beyond ~100k rows.
    """
    rows = conn.execute(
        """
        SELECT p.linkedin_url, p.raw_position, p.raw_company
        FROM people p
        LEFT JOIN people_class pc USING(linkedin_url)
        WHERE pc.linkedin_url IS NULL
        ORDER BY p.linkedin_url
        """
    ).fetchall()
    n_batches = 0
    for i in range(0, len(rows), batch_size):
        slice_ = rows[i:i + batch_size]
        requests = [
            ClassificationRequest(
                linkedin_url=r["linkedin_url"],
                raw_position=r["raw_position"] or "",
                raw_company=r["raw_company"] or "",
            )
            for r in slice_
        ]
        result = classifier.classify_batch(requests)
        n_batches += 1
        batch_usd = (
            result.input_tokens * usd_per_input_mtok / 1_000_000
            + result.output_tokens * usd_per_output_mtok / 1_000_000
        )
        cost.log(
            provider="anthropic", operation="classify_batch",
            units=result.input_tokens + result.output_tokens,
            usd_cost=batch_usd,
            context=f"batch_size={len(requests)}",
        )
        ts = now_iso()
        with conn:
            conn.executemany(
                """
                INSERT INTO people_class(
                  linkedin_url, role_bucket, seniority,
                  classified_at, classifier_model
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(linkedin_url) DO UPDATE SET
                  role_bucket=excluded.role_bucket,
                  seniority=excluded.seniority,
                  classified_at=excluded.classified_at,
                  classifier_model=excluded.classifier_model
                """,
                [
                    (c["linkedin_url"], c["role_bucket"], c["seniority"],
                     ts, model)
                    for c in result.classifications
                ],
            )
    return n_batches
