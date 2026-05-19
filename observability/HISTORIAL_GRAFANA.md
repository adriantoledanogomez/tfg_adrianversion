# Historial SQLite + Grafana (opcional)

Esta capa **no sustituye** tu `docker-compose.yml` ni tus manifiestos K8s actuales.  
Por defecto el backend sigue igual: `HISTORY_ENABLED=0` (no escribe nada).

## Qué hace

1. Tras cada `/api/generar-grafico` exitoso, guarda en SQLite:
   - cabecera de la petición (URL, prompt, modelo, duración…)
   - filas normalizadas (`etiqueta`, `valor`) para graficar en Grafana
2. Grafana lee el mismo archivo `.db` montado en solo lectura.

## Docker Compose (recomendado para practicar)

```powershell
cd TFG
.\activar-historial-compose.ps1
```

O manualmente:

```powershell
docker compose -f docker-compose.yml -f docker-compose.historial.yml up -d --build
```

- App: http://localhost:8501  
- Grafana: http://localhost:3000 (usuario `admin`, contraseña `admin`)  
- Dashboard: carpeta **TFG** → **TFG · Historial de extracciones**

Genera 2–3 gráficos en Streamlit y refresca Grafana (cada 30 s o F5).

### Comprobar que guarda

```powershell
curl http://localhost:8000/api/historial
```

Debe devolver `"enabled": true` y una lista `items`.

### Volver al modo sin historial

```powershell
docker compose -f docker-compose.yml up -d
```

(sin el fichero `docker-compose.historial.yml`)

## Kubernetes

```powershell
cd TFG
cd backend
docker build -t tfg-backend:v1 .
cd ..
.\k8s\observability\activar-grafana.ps1
kubectl rollout restart deployment/backend-deployment
kubectl rollout restart deployment/grafana-deployment
```

1. Reconstruir backend (incluye `historial.py`):

```yaml
env:
  - name: HISTORY_ENABLED
    value: "1"
  - name: HISTORY_DB_PATH
    value: /data/historial.db
volumeMounts:
  - name: historial-data
    mountPath: /data
volumes:
  - name: historial-data
    persistentVolumeClaim:
      claimName: historial-pvc
```

2. Aplicar `k8s/observability/` (Grafana + PVC) cuando quieras; no toca ollama/frontend/backend.yml originales si usas ficheros aparte.

3. Port-forward Grafana:

```powershell
kubectl port-forward svc/grafana 3000:3000
```

## Variables de entorno (backend)

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `HISTORY_ENABLED` | `0` | `1` / `true` para activar SQLite |
| `HISTORY_DB_PATH` | `/data/historial.db` | Ruta del fichero |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Se guarda en cada fila del historial |

## Notas para la memoria del TFG

- Grafana aquí es **analítica de negocio** (datos extraídos), no sustituye Prometheus para métricas de infraestructura.
- Si el plugin SQLite no carga, revisa logs de Grafana: `GF_INSTALL_PLUGINS=frser-sqlite-datasource`.
- La IA puede seguir equivocándose; Grafana solo **visualiza lo guardado**.
