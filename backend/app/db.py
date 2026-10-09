"""Database access layer for PostgreSQL 16 + PostGIS 3.4."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sue:sue@localhost:5433/sue",
)

_pool: ConnectionPool | None = None


def get_db_pool() -> ConnectionPool:
    """Return the singleton connection pool."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def get_db_connection() -> Generator[Connection[dict[str, Any]], None, None]:
    """Yield a database connection from the pool, or raise ConnectionError if unreachable."""
    pool = get_db_pool()
    try:
        with pool.connection() as conn:
            yield conn
    except Exception as exc:
        logger.warning("Database connection failed: %s", exc)
        raise ConnectionError(f"Database unavailable: {exc}") from exc


def is_db_connected() -> bool:
    """Check whether PostGIS is reachable and ready."""
    try:
        import psycopg

        with psycopg.connect(DATABASE_URL, connect_timeout=1) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PostGIS_Full_Version();")
                res = cur.fetchone()
                return res is not None
    except Exception:
        return False


def save_utility_feature(feature_dict: dict[str, Any]) -> str:
    """Insert an ASCE 75-22 utility feature into PostGIS."""
    g_type = feature_dict["geometry_type"]
    table_map = {
        "Point": "utility_points",
        "LineString": "utility_segments",
        "Polygon": "utility_areas",
        "3DObject": "utility_objects",
    }
    table = table_map.get(g_type, "utility_segments")

    sql = f"""
    INSERT INTO {table} (
        id, feature_type, quality_level, horizontal_accuracy, vertical_accuracy,
        geometry_type, material, utility_type, owner, nominal_diameter_mm,
        source_srid, geom
    ) VALUES (
        %(id)s, %(feature_type)s, %(quality_level)s, %(horizontal_accuracy)s, %(vertical_accuracy)s,
        %(geometry_type)s, %(material)s, %(utility_type)s, %(owner)s, %(nominal_diameter_mm)s,
        %(source_srid)s, ST_GeomFromGeoJSON(%(geom)s)
    ) RETURNING id;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    **feature_dict,
                    "geom": json.dumps(feature_dict["geom"]),
                },
            )
            row = cur.fetchone()
            conn.commit()
            return str(row["id"])  # type: ignore[index]
