# Levanta app + historial SQLite + Grafana (Docker Compose)
# Ejecutar desde la carpeta TFG

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Parando stack anterior (si existe)..."
docker compose down 2>$null

Write-Host "==> Levantando con historial + Grafana..."
docker compose -f docker-compose.yml -f docker-compose.historial.yml up -d --build

Write-Host ""
Write-Host "Listo. Espera ~60s la primera vez (Grafana instala plugin SQLite)."
Write-Host "  Streamlit:  http://localhost:8501"
Write-Host "  Grafana:    http://localhost:3000  (admin / admin)"
Write-Host "  Historial:  Invoke-RestMethod http://localhost:8000/api/historial"
Write-Host ""
Write-Host "Genera 2-3 graficos en Streamlit y abre el dashboard TFG en Grafana."
