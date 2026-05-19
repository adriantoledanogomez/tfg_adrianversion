"""Persistencia opcional de extracciones en SQLite (no afecta al flujo si está desactivada)."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Optional

HISTORY_ENABLED = os.getenv("HISTORY_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "/data/historial.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extracciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    url TEXT NOT NULL,
    prompt TEXT NOT NULL,
    modelo TEXT NOT NULL,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error_message TEXT,
    clave_etiquetas TEXT,
    clave_valores TEXT,
    filas_count INTEGER NOT NULL DEFAULT 0,
    datos_json TEXT
);

CREATE TABLE IF NOT EXISTS extraccion_filas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extraccion_id INTEGER NOT NULL,
    etiqueta TEXT NOT NULL,
    valor REAL,
    FOREIGN KEY (extraccion_id) REFERENCES extracciones(id)
);

CREATE INDEX IF NOT EXISTS idx_extracciones_created_at ON extracciones(created_at);
CREATE INDEX IF NOT EXISTS idx_filas_extraccion_id ON extraccion_filas(extraccion_id);
"""


def _connect() -> sqlite3.Connection:
    parent = os.path.dirname(HISTORY_DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    # Facilita que Grafana (otro proceso/pod) lea el .db sin ficheros WAL extra
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    if not HISTORY_ENABLED:
        return
    with closing(_connect()) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _flatten_datos(datos: Any) -> tuple[Optional[str], Optional[str], list[tuple[str, Optional[float]]]]:
    if not isinstance(datos, dict) or len(datos) < 2:
        return None, None, []
    keys = list(datos.keys())
    k_labels, k_values = keys[0], keys[1]
    labels, values = datos[k_labels], datos[k_values]
    if not isinstance(labels, list) or not isinstance(values, list):
        return k_labels, k_values, []
    rows: list[tuple[str, Optional[float]]] = []
    for raw_label, raw_val in zip(labels, values):
        label = str(raw_label) if raw_label is not None else ""
        try:
            num = float(raw_val) if raw_val is not None and str(raw_val).strip() != "" else None
        except (TypeError, ValueError):
            num = None
        rows.append((label, num))
    return k_labels, k_values, rows


def guardar_extraccion(
    *,
    url: str,
    prompt: str,
    modelo: str,
    datos: Any,
    duration_ms: Optional[int],
    status: str = "success",
    error_message: Optional[str] = None,
) -> Optional[int]:
    if not HISTORY_ENABLED:
        return None
    try:
        init_db()
        k_labels, k_values, rows = _flatten_datos(datos)
        created_at = datetime.now(timezone.utc).isoformat()
        datos_json = json.dumps(datos, ensure_ascii=False) if datos is not None else None

        with closing(_connect()) as conn:
            cur = conn.execute(
                """
                INSERT INTO extracciones (
                    created_at, url, prompt, modelo, duration_ms, status,
                    error_message, clave_etiquetas, clave_valores, filas_count, datos_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    url,
                    prompt,
                    modelo,
                    duration_ms,
                    status,
                    error_message,
                    k_labels,
                    k_values,
                    len(rows),
                    datos_json,
                ),
            )
            extraccion_id = int(cur.lastrowid)
            conn.executemany(
                """
                INSERT INTO extraccion_filas (extraccion_id, etiqueta, valor)
                VALUES (?, ?, ?)
                """,
                [(extraccion_id, etiqueta, valor) for etiqueta, valor in rows],
            )
            conn.commit()
            return extraccion_id
    except Exception as exc:
        print(f"[historial] No se pudo guardar la extracción: {exc}")
        return None


def listar_extracciones(limit: int = 20) -> list[dict[str, Any]]:
    if not HISTORY_ENABLED:
        return []
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, url, prompt, modelo, duration_ms, status, filas_count
            FROM extracciones
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [dict(r) for r in rows]


def diagnostico_db() -> dict[str, Any]:
    if not HISTORY_ENABLED:
        return {"enabled": False, "path": HISTORY_DB_PATH}
    info: dict[str, Any] = {
        "enabled": True,
        "path": HISTORY_DB_PATH,
        "exists": os.path.exists(HISTORY_DB_PATH),
        "size_bytes": 0,
        "extracciones": 0,
        "filas": 0,
    }
    if info["exists"]:
        info["size_bytes"] = os.path.getsize(HISTORY_DB_PATH)
    try:
        init_db()
        with closing(_connect()) as conn:
            info["extracciones"] = conn.execute(
                "SELECT COUNT(*) FROM extracciones"
            ).fetchone()[0]
            info["filas"] = conn.execute(
                "SELECT COUNT(*) FROM extraccion_filas"
            ).fetchone()[0]
    except Exception as exc:
        info["error"] = str(exc)
    return info


def estadisticas_resumen() -> dict[str, Any]:
    if not HISTORY_ENABLED:
        return {"enabled": False}
    init_db()
    with closing(_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM extracciones").fetchone()[0]
        avg_row = conn.execute(
            "SELECT ROUND(AVG(duration_ms), 0) FROM extracciones WHERE duration_ms IS NOT NULL"
        ).fetchone()
    return {
        "enabled": True,
        "total_extracciones": int(total),
        "promedio_ms": float(avg_row[0]) if avg_row[0] is not None else None,
    }


def listar_filas_grafana(limit: int = 500) -> list[dict[str, Any]]:
    """Filas planas para Grafana (Infinity / JSON API) sin compartir el .db."""
    if not HISTORY_ENABLED:
        return []
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT
                e.id AS extraccion_id,
                e.created_at,
                e.modelo,
                substr(e.url, 1, 80) AS url,
                f.etiqueta,
                f.valor
            FROM extraccion_filas f
            JOIN extracciones e ON e.id = f.extraccion_id
            ORDER BY e.id DESC, f.id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 2000)),),
        ).fetchall()
    return [dict(r) for r in rows]


def listar_extracciones_tabla(limit: int = 25) -> list[dict[str, Any]]:
    if not HISTORY_ENABLED:
        return []
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, substr(url, 1, 60) AS url, modelo,
                   filas_count, duration_ms, status
            FROM extracciones
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [dict(r) for r in rows]
