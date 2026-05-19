# Grafana muestra "No data" pero /api/historial sí

## Diagnóstico rápido (2 minutos)

### 1) ¿El backend tiene datos en SQLite?

Con port-forward del backend:

```powershell
kubectl port-forward svc/backend 8000:8000
```

Navegador o PowerShell:

```text
http://localhost:8000/api/historial/diagnostico
```

Debe mostrar algo como:

```json
{
  "enabled": true,
  "exists": true,
  "size_bytes": 12345,
  "extracciones": 3,
  "filas": 24
}
```

- Si `filas` es **0**: la IA devolvió JSON sin listas válidas; genera otro gráfico con un prompt más estricto.
- Si `exists` es **false**: el historial no está escribiendo en `/data`.

### 2) ¿Grafana ve el mismo archivo .db?

```powershell
kubectl exec deploy/grafana-deployment -- ls -la /data
kubectl exec deploy/backend-deployment -- ls -la /data
```

**Los dos** deben listar `historial.db` con tamaño similar.

| backend tiene .db | grafana /data vacío | Causa |
|-------------------|---------------------|--------|
| Sí | Sí | PVC no compartido → usar plan B (API HTTP abajo) |
| Sí | Sí (mismo tamaño) | Plugin SQLite o consulta → paso 3 |

### 3) Probar SQL en Grafana Explore

**Importante:** la ruta del datasource debe ser solo `/data/historial.db` (sin `?` ni parámetros).  
Si ves `near "?": syntax error`, es casi siempre por `?_pragma=...` en la ruta o por macros de tiempo.

1. Grafana → **Explore**
2. Datasource: **TFG Historial SQLite**
3. Arriba a la derecha: pon el rango de tiempo en **"Ignore time range"** o desactiva filtro temporal si aparece.
4. Consulta (copiar tal cual, sin `?`):

```sql
SELECT COUNT(*) AS total FROM extracciones
```

5. **Run query**

- Si da error → revisa **Connections → Data sources → Save & test**
- Si devuelve número → el dashboard está mal enlazado; reimporta el dashboard
- Si devuelve 0 pero diagnostico dice filas > 0 → Grafana lee **otro** fichero (volumen distinto)

---

## Plan B (recomendado en K8s): leer por HTTP, sin SQLite en Grafana

Grafana llama al backend por red (como Streamlit). No hace falta compartir el `.db`.

### A) Reconstruir backend (nuevos endpoints)

```powershell
cd backend
docker build -t tfg-backend:v1 .
kubectl rollout restart deployment/backend-deployment
```

### B) Comprobar API

```text
http://localhost:8000/api/historial/estadisticas
http://localhost:8000/api/historial/filas
http://localhost:8000/api/historial/tabla
```

### C) Instalar plugin Infinity en Grafana

Edita `k8s/observability/grafana.yml` y añade al env:

```yaml
GF_INSTALL_PLUGINS: frser-sqlite-datasource,yesoreyeram-infinity-datasource
```

Luego:

```powershell
kubectl apply -f k8s/observability/grafana.yml
kubectl rollout restart deployment/grafana-deployment
```

Espera 2–3 minutos.

### D) Crear datasource en Grafana (UI)

1. **Connections → Add new connection → Infinity**
2. Nombre: `TFG Backend API`
3. Base URL: `http://backend:8000`  (nombre del Service en K8s)
4. Save & test

### E) Panel de prueba

1. **Explore** → datasource **Infinity**
2. Type: **JSON**
3. URL: `/api/historial/tabla`
4. Parser: JSON → Root: `rows`
5. Debe verse la tabla de extracciones

Para un **Stat** de total:

- URL: `/api/historial/estadisticas`
- Root: `total_extracciones`

---

## Caracteres raros (?? en títulos)

Es un problema de codificación UTF-8 al provisionar el dashboard. No afecta a los datos. Puedes renombrar los paneles a mano en Grafana.

---

## Resumen

| Síntoma | Acción |
|---------|--------|
| diagnostico `filas: 0` | Mejorar prompt / generar gráficos de nuevo |
| grafana `/data` vacío | Plan B Infinity + `http://backend:8000` |
| Explore SQL funciona, dashboard no | Reimportar dashboard o recrear paneles |
| Todo OK en Explore | Refrescar dashboard (F5) |
