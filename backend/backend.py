import os
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from historial import (
    HISTORY_ENABLED,
    diagnostico_db,
    estadisticas_resumen,
    guardar_extraccion,
    init_db,
    listar_extracciones,
    listar_extracciones_tabla,
    listar_filas_grafana,
)
from pipeline import OLLAMA_MODEL, PipelineError, procesar_consulta

# sync = procesar en este pod (Docker Compose)
# kubernetes = un Job efímero por consulta (TFG / K8s)
WORKER_MODE = os.getenv("WORKER_MODE", "sync").strip().lower()

app = FastAPI(
    title="Backend TFG IA",
    description="Orquestador API. Con WORKER_MODE=kubernetes lanza un Job por consulta.",
)


@app.on_event("startup")
def _startup_historial() -> None:
    init_db()


class PeticionUsuario(BaseModel):
    url: str
    prompt: str


def _procesar_sync(url: str, prompt: str) -> tuple[dict, int]:
    inicio = time.perf_counter()
    try:
        datos = procesar_consulta(url, prompt)
        duration_ms = int((time.perf_counter() - inicio) * 1000)
        return datos, duration_ms
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _procesar_kubernetes(url: str, prompt: str) -> tuple[dict, int, str]:
    from k8s_jobs import ejecutar_consulta_en_job

    try:
        resultado = ejecutar_consulta_en_job(url, prompt)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Error al ejecutar el Job: {exc}"
        ) from exc

    job_id = resultado.get("job_id", "")
    duration_ms = int(resultado.get("duration_ms") or 0)

    if resultado.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=resultado.get("error", "Error en el worker"),
        )

    return resultado.get("data") or {}, duration_ms, job_id


@app.get("/api/modo")
def api_modo():
    return {
        "worker_mode": WORKER_MODE,
        "ollama_model": OLLAMA_MODEL,
        "descripcion": (
            "kubernetes = un Job por consulta; sync = procesamiento en este pod"
        ),
    }


@app.get("/api/ollama/estado")
def api_ollama_estado():
    """Comprueba que Ollama responde y si el modelo configurado está descargado."""
    import requests as req
    from pipeline import OLLAMA_MODEL, OLLAMA_URL

    base = OLLAMA_URL.rstrip("/")
    out: dict = {"ollama_url": base, "modelo_configurado": OLLAMA_MODEL}
    try:
        tags = req.get(f"{base}/api/tags", timeout=10)
        out["tags_http"] = tags.status_code
        if tags.status_code == 200:
            nombres = [m.get("name", "") for m in tags.json().get("models", [])]
            out["modelos_instalados"] = nombres
            out["modelo_disponible"] = any(
                OLLAMA_MODEL in n or n.startswith(OLLAMA_MODEL.split(":")[0])
                for n in nombres
            )
        else:
            out["tags_error"] = tags.text[:300]
    except Exception as exc:
        out["error"] = str(exc)
        out["sugerencia"] = (
            "Comprueba que el pod ollama está Running: kubectl get pods -l app=ollama"
        )
    return out


@app.get("/api/k8s-diagnostico")
def api_k8s_diagnostico():
    if WORKER_MODE != "kubernetes":
        return {"worker_mode": WORKER_MODE, "mensaje": "No aplica fuera de kubernetes"}
    from k8s_jobs import diagnostico_k8s_cliente

    return diagnostico_k8s_cliente()


@app.get("/api/historial")
def api_historial(limit: int = 20):
    if not HISTORY_ENABLED:
        return {"enabled": False, "items": []}
    return {"enabled": True, "items": listar_extracciones(limit=limit)}


@app.get("/api/historial/diagnostico")
def api_historial_diagnostico():
    return diagnostico_db()


@app.get("/api/historial/estadisticas")
def api_historial_estadisticas():
    return estadisticas_resumen()


@app.get("/api/historial/filas")
def api_historial_filas(limit: int = 500):
    if not HISTORY_ENABLED:
        return {"enabled": False, "rows": []}
    return {"enabled": True, "rows": listar_filas_grafana(limit=limit)}


@app.get("/api/historial/tabla")
def api_historial_tabla(limit: int = 25):
    if not HISTORY_ENABLED:
        return {"enabled": False, "rows": []}
    return {"enabled": True, "rows": listar_extracciones_tabla(limit=limit)}


@app.post("/api/generar-grafico")
def procesar_peticion(peticion: PeticionUsuario):
    print(f"[orquestador] modo={WORKER_MODE} url={peticion.url}")

    job_id = ""
    try:
        if WORKER_MODE == "kubernetes":
            datos, duration_ms, job_id = _procesar_kubernetes(
                peticion.url, peticion.prompt
            )
        else:
            datos, duration_ms = _procesar_sync(peticion.url, peticion.prompt)

        guardar_extraccion(
            url=peticion.url,
            prompt=peticion.prompt,
            modelo=OLLAMA_MODEL,
            datos=datos,
            duration_ms=duration_ms,
            status="success",
        )
        body = {"status": "success", "data": datos, "worker_mode": WORKER_MODE}
        if job_id:
            body["job_id"] = job_id
        return body

    except HTTPException as exc:
        duration_ms = 0
        guardar_extraccion(
            url=peticion.url,
            prompt=peticion.prompt,
            modelo=OLLAMA_MODEL,
            datos=None,
            duration_ms=duration_ms,
            status="error",
            error_message=str(exc.detail),
        )
        raise
