import json
import os
import time

import requests
from bs4 import BeautifulSoup
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

app = FastAPI(title="Backend TFG IA")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")


@app.on_event("startup")
def _startup_historial() -> None:
    init_db()


class PeticionUsuario(BaseModel):
    url: str
    prompt: str


def extraer_texto_web(url: str) -> str:
    """Descarga la web y extrae solo el texto limpio."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()

        texto_limpio = soup.get_text(separator=" ", strip=True)
        return texto_limpio[:7500]

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al raspar la URL: {str(e)}")


def llamar_ollama(texto_contexto: str, prompt_usuario: str) -> dict:
    """Envía el texto y el prompt a Ollama forzando salida JSON estricta."""
    prompt_completo = f"""
    Eres un extractor de datos implacable. Tu ÚNICA función es leer el texto y extraer lo que pide el usuario en un JSON puro.

    REGLA 1: No incluyas claves como 'status', 'message', 'data' o 'title'.
    REGLA 2: El formato DEBE ser estrictamente un diccionario.
    REGLA 3: ESTÁ ESTRICTAMENTE PROHIBIDO usar los datos del ejemplo en tu respuesta.
    REGLA 4: El JSON debe contener EXACTAMENTE DOS CLAVES. Una clave para los nombres (como lista de textos) y otra clave para los valores (como lista de números).
    REGLA 5: Ambas listas DEBEN tener exactamente el mismo número de elementos (misma longitud). NO uses diccionarios anidados.

    EJEMPLO DE ESTRUCTURA (NO USAR ESTOS DATOS):
    {{
        "Entidad": ["Ejemplo A", "Ejemplo B"],
        "Valor": [100, 200]
    }}

    INSTRUCCIÓN DEL USUARIO: {prompt_usuario}

    TEXTO FUENTE:
    {texto_contexto}
    """

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt_completo,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0},
    }

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=600
        )
        response.raise_for_status()
        respuesta_ia = response.json().get("response", "{}")
        return json.loads(respuesta_ia)

    except Exception as e:
        print(f"Error interno en Ollama: {str(e)}")
        return {"Error": ["No se pudo generar el JSON"], "Detalle": [str(e)]}


@app.get("/api/historial")
def api_historial(limit: int = 20):
    """Solo responde con datos si HISTORY_ENABLED=1 (para depuración / TFG)."""
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
    inicio = time.perf_counter()
    print(f"1. Procesando URL: {peticion.url}")

    try:
        texto_web = extraer_texto_web(peticion.url)
        print("2. Texto extraído. Llamando a Ollama...")
        datos_extraidos = llamar_ollama(texto_web, peticion.prompt)
        print("3. Datos procesados correctamente.")
        duration_ms = int((time.perf_counter() - inicio) * 1000)
        guardar_extraccion(
            url=peticion.url,
            prompt=peticion.prompt,
            modelo=OLLAMA_MODEL,
            datos=datos_extraidos,
            duration_ms=duration_ms,
            status="success",
        )
        return {"status": "success", "data": datos_extraidos}

    except HTTPException:
        duration_ms = int((time.perf_counter() - inicio) * 1000)
        guardar_extraccion(
            url=peticion.url,
            prompt=peticion.prompt,
            modelo=OLLAMA_MODEL,
            datos=None,
            duration_ms=duration_ms,
            status="error",
            error_message="scrape_failed",
        )
        raise
