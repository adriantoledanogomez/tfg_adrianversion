import os
import re
import unicodedata
from typing import Any, Optional

import altair as alt
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components

# --- Config -----------------------------------------------------------------
st.set_page_config(
    page_title="Extractor IA · TFG",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000").rstrip("/")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
# Debe ser >= JOB_TIMEOUT_SEC del backend (Job + Ollama en CPU puede tardar mucho)
API_TIMEOUT_SEC = int(os.environ.get("API_TIMEOUT_SEC", "2400"))

SPAIN_CCAA_GEOJSON_URL = (
    "https://cdn.jsdelivr.net/gh/codeforgermany/click_that_hood@master/"
    "public/data/spain-communities.geojson"
)

_GEOJSON_CCAA_NAMES = frozenset(
    {
        "Andalucia",
        "Aragon",
        "Asturias",
        "Baleares",
        "Canarias",
        "Cantabria",
        "Castilla-La Mancha",
        "Castilla-Leon",
        "Cataluña",
        "Ceuta",
        "Extremadura",
        "Galicia",
        "La Rioja",
        "Madrid",
        "Melilla",
        "Murcia",
        "Navarra",
        "Pais Vasco",
        "Valencia",
    }
)


def _ascii_key(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text))
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower().strip()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Variantes habituales (IA / prensa) → nombre en properties.name del GeoJSON
_EXTRA_ALIASES: dict[str, str] = {
    "andalucia": "Andalucia",
    "comunidad autonoma de andalucia": "Andalucia",
    "aragon": "Aragon",
    "comunidad autonoma de aragon": "Aragon",
    "asturias": "Asturias",
    "principado de asturias": "Asturias",
    "illes balears": "Baleares",
    "islas baleares": "Baleares",
    "illes balears islas baleares": "Baleares",
    "baleares": "Baleares",
    "canarias": "Canarias",
    "islas canarias": "Canarias",
    "cantabria": "Cantabria",
    "castilla la mancha": "Castilla-La Mancha",
    "castilla y leon": "Castilla-Leon",
    "castilla leon": "Castilla-Leon",
    "cataluna": "Cataluña",
    "catalunya": "Cataluña",
    "generalitat": "Cataluña",
    "ceuta": "Ceuta",
    "extremadura": "Extremadura",
    "galicia": "Galicia",
    "la rioja": "La Rioja",
    "rioja": "La Rioja",
    "madrid": "Madrid",
    "comunidad de madrid": "Madrid",
    "melilla": "Melilla",
    "murcia": "Murcia",
    "region de murcia": "Murcia",
    "navarra": "Navarra",
    "comunidad foral de navarra": "Navarra",
    "nafarroa": "Navarra",
    "pais vasco": "Pais Vasco",
    "euskadi": "Pais Vasco",
    "comunidad autonoma del pais vasco": "Pais Vasco",
    "comunidad valenciana": "Valencia",
    "comunitat valenciana": "Valencia",
    "valencia": "Valencia",
}


def map_label_to_ccaa_geojson(raw: Any) -> Optional[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    key = _ascii_key(s)
    if key in _EXTRA_ALIASES:
        return _EXTRA_ALIASES[key]
    # Coincidencia directa con nombre del GeoJSON
    for gn in _GEOJSON_CCAA_NAMES:
        if _ascii_key(gn) == key:
            return gn
    # Contiene subcadena (ej. "Provincia de Sevilla (Andalucía)" → Andalucia)
    for gn in _GEOJSON_CCAA_NAMES:
        gk = _ascii_key(gn)
        if gk and (gk in key or key in gk):
            return gn
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def load_spain_ccaa_geojson() -> dict[str, Any]:
    r = requests.get(SPAIN_CCAA_GEOJSON_URL, timeout=60)
    r.raise_for_status()
    return r.json()


def inject_custom_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700&display=swap');
        html, body, [class*="st-"] { font-family: 'DM Sans', system-ui, sans-serif; }
        .hero {
            background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #2563eb 100%);
            color: #f8fafc;
            padding: 1.75rem 1.5rem;
            border-radius: 14px;
            margin-bottom: 1.25rem;
            box-shadow: 0 12px 40px rgba(15, 23, 42, 0.25);
        }
        .hero h1 { margin: 0 0 0.35rem 0; font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em; }
        .hero p { margin: 0; opacity: 0.9; font-size: 0.98rem; line-height: 1.45; }
        div[data-testid="stExpander"] details { border-radius: 10px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_choropleth_spain(df: pd.DataFrame, name_col: str, value_col: str) -> None:
    try:
        geojson = load_spain_ccaa_geojson()
    except Exception as e:
        st.error(f"No se pudo cargar el mapa de España (GeoJSON): {e}")
        return

    work = df[[name_col, value_col]].copy()
    work["_ccaa"] = work[name_col].map(map_label_to_ccaa_geojson)
    matched = work.dropna(subset=["_ccaa"])
    if matched.empty:
        st.warning(
            "No se pudo emparejar ninguna fila con una **comunidad autónoma** del mapa. "
            "Pide en el prompt nombres de CCAA (ej. Andalucía, Cataluña, Madrid…)."
        )
        return

    unmatched = work[work["_ccaa"].isna()]
    if not unmatched.empty:
        st.caption(
            f"Filas sin emparejar al mapa: **{len(unmatched)}** "
            f"(muestra: {', '.join(map(str, unmatched[name_col].head(5).tolist()))})"
        )

    fig = px.choropleth(
        matched,
        geojson=geojson,
        locations="_ccaa",
        featureidkey="properties.name",
        color=value_col,
        color_continuous_scale="Blues",
        labels={value_col: str(value_col), "_ccaa": "Comunidad"},
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(
        margin=dict(l=0, r=0, t=32, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        coloraxis_colorbar=dict(title=""),
        title=dict(
            text="Mapa coroplético · España (comunidades autónomas)",
            font=dict(size=16),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_bar_altair(df: pd.DataFrame, name_col: str, value_col: str) -> None:
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4, color="#2563eb")
        .encode(
            x=alt.X(name_col, sort=None, title=str(name_col)),
            y=alt.Y(value_col, title=str(value_col)),
            tooltip=[name_col, value_col],
        )
        .properties(height=380)
        .configure_axis(labelLimit=200)
    )
    st.altair_chart(chart, use_container_width=True)


def render_heatmap_bars(df: pd.DataFrame, name_col: str, value_col: str) -> None:
    fig = px.bar(
        df,
        x=name_col,
        y=value_col,
        color=value_col,
        color_continuous_scale="Viridis",
        labels={name_col: str(name_col), value_col: str(value_col)},
    )
    fig.update_layout(
        xaxis_tickangle=-35,
        margin=dict(t=40, b=120),
        showlegend=False,
        title="Barras con escala de color (heatmap)",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,0.6)",
    )
    st.plotly_chart(fig, use_container_width=True)


def normalize_backend_payload(datos_backend: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(datos_backend, dict):
        return datos_backend
    claves = list(datos_backend.keys())
    if len(claves) < 2:
        return datos_backend
    lista1 = datos_backend[claves[0]]
    lista2 = datos_backend[claves[1]]
    min_length = min(len(lista1), len(lista2))
    datos_backend[claves[0]] = lista1[:min_length]
    datos_backend[claves[1]] = lista2[:min_length]
    return datos_backend


# --- UI ---------------------------------------------------------------------
inject_custom_css()

st.markdown(
    """
    <div class="hero">
        <h1>Extractor de datos con IA</h1>
        <p>Raspa una URL, describe qué quieres extraer y visualiza el resultado con gráficos
        profesionales o un mapa de España por comunidades autónomas.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Configuración")
    backend_override = st.text_input(
        "URL del backend",
        value=BACKEND_URL,
        help="En Docker/K8s suele ser http://backend:8000. En local, http://localhost:8000",
    )
    tipo_grafico = st.radio(
        "Tipo de visualización",
        (
            "Barras (Altair)",
            "Mapa España (CCAA)",
            "Barras con gradiente (estilo heatmap)",
        ),
        help="El mapa requiere que la primera columna sean comunidades autónomas reconocibles.",
    )
    tipo_orden = st.radio(
        "Orden de los datos",
        ("Orden original", "Mayor a menor", "Menor a mayor"),
    )
    limite_datos = st.number_input(
        "Máx. filas en el gráfico (0 = todas)",
        min_value=0,
        max_value=100,
        value=10,
    )
    st.divider()
    st.markdown("#### Grafana (opcional)")
    grafana_input = st.text_input(
        "URL base de Grafana",
        value=GRAFANA_URL or "",
        placeholder="https://grafana.midominio.com",
        help="Si despliegas Grafana aparte, pega aquí la URL para abrirla o incrustarla.",
    )
    if grafana_input.strip():
        g_url = grafana_input.strip().rstrip("/")
        st.link_button("Abrir Grafana en pestaña nueva", g_url, use_container_width=True)
        if st.checkbox("Intentar incrustar Grafana aquí", value=False):
            st.caption(
                "Muchos servidores bloquean iframes (cabecera X-Frame-Options). "
                "Si ves en blanco, abre con el botón o habilita embedding en Grafana."
            )
            components.iframe(g_url, height=520, scrolling=True)

col_a, col_b = st.columns((1, 1), gap="medium")
with col_a:
    url_input = st.text_input(
        "URL de origen",
        placeholder="https://es.wikipedia.org/wiki/Anexo:Comunidades_autónomas_de_España_por_población",
    )
with col_b:
    prompt_input = st.text_input(
        "Instrucciones para la IA",
        placeholder="Extrae comunidad autónoma y población en miles; nombres oficiales en español.",
    )

run = st.button("Generar análisis", type="primary", use_container_width=True)

if run:
    if not url_input or not prompt_input:
        st.warning("Indica **URL** e **instrucciones** antes de continuar.")
    else:
        endpoint = f"{backend_override.rstrip('/')}/api/generar-grafico"
        with st.spinner(
            f"Procesando (puede tardar 10–20 min en la nube: Job + Ollama). "
            f"Espera hasta {API_TIMEOUT_SEC // 60} min…"
        ):
            try:
                respuesta = requests.post(
                    endpoint,
                    json={"url": url_input, "prompt": prompt_input},
                    timeout=API_TIMEOUT_SEC,
                )
            except requests.exceptions.ConnectionError:
                st.error(
                    "No hay conexión con el backend. Comprueba la URL y que el servicio esté en marcha."
                )
                st.stop()
            except requests.exceptions.ReadTimeout:
                st.error(
                    f"La petición superó el tiempo de espera ({API_TIMEOUT_SEC}s). "
                    "En la VM revisa: `kubectl get jobs -l app=tfg-worker` y "
                    "`kubectl logs -l app=tfg-worker --tail=50`. "
                    "Ollama en CPU puede tardar mucho; prueba un modelo más pequeño (llama3.2:3b)."
                )
                st.stop()

        if respuesta.status_code != 200:
            st.error(f"El backend respondió con código {respuesta.status_code}.")
            st.code(respuesta.text[:2000])
            st.stop()

        body = respuesta.json()
        datos_backend = body.get("data")
        datos_backend = normalize_backend_payload(datos_backend)

        with st.expander("JSON devuelto por la IA", expanded=False):
            st.json(datos_backend)

        try:
            df = pd.DataFrame(datos_backend)
        except Exception as e:
            st.error(f"No se pudo convertir la respuesta a tabla: {e}")
            st.stop()

        st.subheader("Tabla de datos")
        st.dataframe(df, use_container_width=True, hide_index=True)

        cols = df.columns.tolist()
        if len(cols) < 2:
            st.warning("Se necesitan al menos **dos columnas** (etiquetas y valores numéricos).")
            st.stop()

        name_col, value_col = cols[0], cols[1]
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

        if tipo_orden == "Mayor a menor":
            df = df.sort_values(by=value_col, ascending=False)
        elif tipo_orden == "Menor a mayor":
            df = df.sort_values(by=value_col, ascending=True)

        if limite_datos > 0:
            df_plot = df.head(limite_datos)
        else:
            df_plot = df

        st.subheader("Visualización")
        if tipo_grafico == "Barras (Altair)":
            render_bar_altair(df_plot, name_col, value_col)
        elif tipo_grafico == "Mapa España (CCAA)":
            render_choropleth_spain(df_plot, name_col, value_col)
        else:
            render_heatmap_bars(df_plot, name_col, value_col)

else:
    st.info(
        "Elige una fuente con datos tabulares o descriptivos y pide explícitamente **dos columnas**: "
        "nombres y valores numéricos. Para el mapa de España, pide datos por **comunidad autónoma**."
    )
