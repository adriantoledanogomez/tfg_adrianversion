"""Lógica de negocio: scrape web + llamada a Ollama (compartida por API y worker)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
TEXTO_MAX_CHARS = int(os.getenv("TEXTO_MAX_CHARS", "6000"))
OLLAMA_TIMEOUT_SEC = int(os.getenv("OLLAMA_TIMEOUT_SEC", "3600"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
OLLAMA_MAX_FILAS = int(os.getenv("OLLAMA_MAX_FILAS", "15"))
OLLAMA_JSON_RETRIES = int(os.getenv("OLLAMA_JSON_RETRIES", "1"))


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


def _quitar_bloque_markdown(texto: str) -> str:
    t = texto.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _extraer_objeto_json(texto: str) -> str:
    """Primer objeto {...} balanceado (ignora llaves dentro de strings)."""
    texto = _quitar_bloque_markdown(texto)
    inicio = texto.find("{")
    if inicio < 0:
        raise ValueError("No hay objeto JSON en la respuesta")

    profundidad = 0
    en_string = False
    escape = False
    comilla = ""

    for i in range(inicio, len(texto)):
        c = texto[i]
        if en_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == comilla:
                en_string = False
            continue
        if c in ('"', "'"):
            en_string = True
            comilla = c
            continue
        if c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio : i + 1]
    raise ValueError("JSON incompleto (falta cierre de llaves)")


def _cerrar_json_truncado(fragmento: str) -> str:
    """Intenta cerrar listas/objetos si la respuesta se cortó por num_predict."""
    s = fragmento.rstrip()
    # Quita fragmento final roto (coma, comilla abierta, etc.)
    while s and s[-1] not in '}]"0123456789':
        s = s[:-1]
    abiertas_llaves = s.count("{") - s.count("}")
    abiertas_corchetes = s.count("[") - s.count("]")
    if abiertas_corchetes > 0:
        s += "]" * abiertas_corchetes
    if abiertas_llaves > 0:
        s += "}" * abiertas_llaves
    return s


def _reparar_json_sintaxis(raw: str) -> str:
    s = raw.replace("\ufeff", "")
    s = re.sub(r",\s*([}\]])", r"\1", s)  # comas finales
    s = re.sub(r"\bNaN\b", "null", s)
    s = re.sub(r"\bInfinity\b", "null", s)
    return s


def _parsear_respuesta_ia(respuesta_ia: str) -> dict[str, Any]:
    if not respuesta_ia or not str(respuesta_ia).strip():
        raise ValueError("Respuesta vacía")

    candidatos: list[str] = []
    try:
        candidatos.append(_extraer_objeto_json(respuesta_ia))
    except ValueError:
        pass
    candidatos.append(_quitar_bloque_markdown(respuesta_ia))

    ultimo_error: Optional[Exception] = None
    for base in candidatos:
        for variante in (base, _reparar_json_sintaxis(base), _cerrar_json_truncado(base)):
            try:
                v = _reparar_json_sintaxis(variante)
                data = json.loads(v)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError) as exc:
                ultimo_error = exc
                continue

    raise ValueError(f"JSON inválido tras reparación: {ultimo_error}")


def _normalizar_dos_listas(data: dict[str, Any]) -> dict[str, Any]:
    if len(data) < 2:
        raise ValueError("El JSON debe tener al menos dos claves con listas")
    claves = list(data.keys())[:2]
    k1, k2 = claves[0], claves[1]
    l1, l2 = data[k1], data[k2]
    if not isinstance(l1, list) or not isinstance(l2, list):
        raise ValueError("Las dos primeras claves deben ser listas")
    n = min(len(l1), len(l2), OLLAMA_MAX_FILAS)
    if n == 0:
        raise ValueError("Las listas están vacías")
    out: dict[str, Any] = {k1: l1[:n], k2: l2[:n]}
    # Valores numéricos: quitar separadores de miles en strings
    limpios: list[Any] = []
    for v in out[k2]:
        if isinstance(v, (int, float)):
            limpios.append(v)
        elif isinstance(v, str):
            s = v.strip().replace(".", "").replace(",", ".") if v.count(",") == 1 else v
            s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
            try:
                limpios.append(float(s) if "." in s else int(s))
            except ValueError:
                limpios.append(0)
        else:
            limpios.append(0)
    out[k2] = limpios
    return out


def _construir_prompt(texto_contexto: str, prompt_usuario: str, *, compacto: bool) -> str:
    max_filas = OLLAMA_MAX_FILAS
    if compacto:
        return f"""
Devuelve SOLO un JSON válido (sin markdown, sin comentarios).
Exactamente dos claves: una con {max_filas} nombres como máximo (strings) y otra con números.
Misma longitud en ambas listas. Sin comillas sin escapar dentro de los textos.
Si no hay datos claros en el texto, usa listas vacías [].

Usuario: {prompt_usuario}

Texto (resumen):
{texto_contexto[:3500]}
""".strip()

    return f"""
Eres un extractor de datos. Devuelve ÚNICAMENTE un objeto JSON válido (sin ``` ni texto extra).

REGLAS:
- Exactamente DOS claves: nombres (lista de strings) y valores (lista de números).
- Máximo {max_filas} elementos por lista. Misma longitud en ambas.
- Usa solo comillas dobles estándar. Escapa comillas dentro de nombres con \\".
- No inventes cifras: si no están en el texto, devuelve "items": [] y "valores": [].
- Prohibido: claves status, message, data, title, ni objetos anidados.

Ejemplo de forma (NO uses estos datos):
{{"items": ["A", "B"], "valores": [1, 2]}}

INSTRUCCIÓN: {prompt_usuario}

TEXTO FUENTE:
{texto_contexto}
""".strip()


def _post_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }
    endpoint = f"{OLLAMA_URL}/api/generate"
    response = requests.post(endpoint, json=payload, timeout=OLLAMA_TIMEOUT_SEC)
    if response.status_code == 404:
        cuerpo = response.text[:500]
        raise PipelineError(
            f"Ollama respondió 404 en {endpoint}. "
            f"Suele faltar el modelo '{OLLAMA_MODEL}' (ejecuta: "
            f"kubectl exec deploy/ollama-deployment -c ollama -- ollama pull {OLLAMA_MODEL}). "
            f"Detalle: {cuerpo}"
        )
    response.raise_for_status()
    return response.json().get("response", "{}")


def llamar_ollama(texto_contexto: str, prompt_usuario: str) -> dict[str, Any]:
    try:
        intentos = 1 + max(0, OLLAMA_JSON_RETRIES)
        ultimo_parse: Optional[Exception] = None
        for n in range(intentos):
            compacto = n > 0
            prompt = _construir_prompt(
                texto_contexto, prompt_usuario, compacto=compacto
            )
            respuesta_ia = _post_ollama(prompt)
            try:
                parsed = _parsear_respuesta_ia(respuesta_ia)
                return _normalizar_dos_listas(parsed)
            except (ValueError, json.JSONDecodeError) as exc:
                ultimo_parse = exc
                if n + 1 >= intentos:
                    muestra = respuesta_ia[:400].replace("\n", " ")
                    raise PipelineError(
                        f"La IA no devolvió JSON válido: {exc}. "
                        f"Inicio de respuesta: {muestra!r}…"
                    ) from exc
        raise PipelineError(f"La IA no devolvió JSON válido: {ultimo_parse}")
    except PipelineError:
        raise
    except requests.exceptions.ReadTimeout as exc:
        raise PipelineError(
            f"Ollama no terminó la inferencia en {OLLAMA_TIMEOUT_SEC}s "
            f"(modelo={OLLAMA_MODEL}, url={OLLAMA_URL}). "
            "En una VM sin GPU suele tardar mucho: usa un modelo pequeño "
            "(llama3.2:3b) o sube OLLAMA_TIMEOUT_SEC / memoria del pod ollama."
        ) from exc
    except requests.RequestException as exc:
        raise PipelineError(
            f"No se pudo contactar con Ollama en {OLLAMA_URL}: {exc}"
        ) from exc


def procesar_consulta(url: str, prompt: str) -> dict[str, Any]:
    texto = extraer_texto_web(url)
    return llamar_ollama(texto, prompt)
