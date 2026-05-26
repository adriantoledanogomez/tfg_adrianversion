"""Lógica de negocio: scrape web + llamada a Ollama (compartida por API y worker)."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from bs4 import BeautifulSoup

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
TEXTO_MAX_CHARS = int(os.getenv("TEXTO_MAX_CHARS", "7500"))


class PipelineError(Exception):
    """Error recuperable en el pipeline (scrape, Ollama, JSON)."""


def extraer_texto_web(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.extract()
        return soup.get_text(separator=" ", strip=True)[:TEXTO_MAX_CHARS]
    except Exception as exc:
        raise PipelineError(f"Error al raspar la URL: {exc}") from exc


def llamar_ollama(texto_contexto: str, prompt_usuario: str) -> dict[str, Any]:
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

    endpoint = f"{OLLAMA_URL}/api/generate"
    try:
        response = requests.post(endpoint, json=payload, timeout=600)
        if response.status_code == 404:
            cuerpo = response.text[:500]
            raise PipelineError(
                f"Ollama respondió 404 en {endpoint}. "
                f"Suele faltar el modelo '{OLLAMA_MODEL}' (ejecuta: "
                f"kubectl exec deploy/ollama-deployment -c ollama -- ollama pull {OLLAMA_MODEL}). "
                f"Detalle: {cuerpo}"
            )
        response.raise_for_status()
        respuesta_ia = response.json().get("response", "{}")
        return json.loads(respuesta_ia)
    except PipelineError:
        raise
    except requests.RequestException as exc:
        raise PipelineError(
            f"No se pudo contactar con Ollama en {OLLAMA_URL}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"La IA no devolvió JSON válido: {exc}") from exc


def procesar_consulta(url: str, prompt: str) -> dict[str, Any]:
    texto = extraer_texto_web(url)
    return llamar_ollama(texto, prompt)
