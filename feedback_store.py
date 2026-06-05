from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from cbioportal_search import get_database_url

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_feedback (
    id BIGSERIAL PRIMARY KEY,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL DEFAULT '',
    email TEXT,
    page_section TEXT,
    analysis_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at
    ON user_feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_feedback_rating
    ON user_feedback(rating);
"""


def init_feedback_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def save_user_feedback(
    *,
    rating: int,
    comment: str,
    email: str | None = None,
    page_section: str | None = None,
    analysis_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_email = (email or "").strip() or None
    normalized_comment = (comment or "").strip()
    snapshot = analysis_snapshot or {}

    conn = psycopg2.connect(get_database_url())
    try:
        init_feedback_schema(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO user_feedback (
                    rating,
                    comment,
                    email,
                    page_section,
                    analysis_snapshot
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, rating, comment, email, page_section, analysis_snapshot, created_at
                """,
                (
                    rating,
                    normalized_comment,
                    normalized_email,
                    page_section,
                    Json(snapshot),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    created_at = row["created_at"]
    return {
        "id": row["id"],
        "rating": row["rating"],
        "comment": row["comment"],
        "email": row["email"],
        "page_section": row["page_section"],
        "analysis_snapshot": row["analysis_snapshot"] or {},
        "created_at": created_at.isoformat() if created_at else None,
    }
