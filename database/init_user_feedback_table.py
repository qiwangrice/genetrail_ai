from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cbioportal_search import get_database_url
from feedback_store import init_feedback_schema


def main() -> int:
    load_dotenv()
    conn = psycopg2.connect(get_database_url())
    try:
        init_feedback_schema(conn)
    finally:
        conn.close()

    print("Neon Postgres now contains user_feedback table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
