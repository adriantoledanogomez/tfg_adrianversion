"""Contenedor efímero: una consulta por ejecución (Kubernetes Job)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pipeline import PipelineError, procesar_consulta

JOBS_ROOT = Path(os.getenv("JOBS_ROOT", "/data/jobs"))


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip()
    url = os.environ.get("URL", "").strip()
    prompt = os.environ.get("PROMPT", "").strip()

    if not job_id or not url or not prompt:
        print("Faltan variables JOB_ID, URL o PROMPT", file=sys.stderr)
        return 1

    out_dir = JOBS_ROOT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "result.json"

    inicio = time.perf_counter()
    payload: dict = {
        "job_id": job_id,
        "url": url,
        "prompt": prompt,
    }

    try:
        datos = procesar_consulta(url, prompt)
        payload.update(
            {
                "status": "success",
                "data": datos,
                "duration_ms": int((time.perf_counter() - inicio) * 1000),
            }
        )
        exit_code = 0
    except PipelineError as exc:
        payload.update(
            {
                "status": "error",
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - inicio) * 1000),
            }
        )
        exit_code = 1
    except Exception as exc:
        payload.update(
            {
                "status": "error",
                "error": f"Error inesperado: {exc}",
                "duration_ms": int((time.perf_counter() - inicio) * 1000),
            }
        )
        exit_code = 1

    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Resultado escrito en {result_path} ({payload.get('status')})")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
