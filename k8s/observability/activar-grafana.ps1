# Ejecutar desde la carpeta TFG:
#   .\k8s\observability\activar-grafana.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Root

Write-Host "==> ConfigMap del dashboard"
kubectl create configmap grafana-dashboard-tfg `
  --from-file=tfg-historial.json=observability/grafana/provisioning/dashboards/json/tfg-historial.json `
  --dry-run=client -o yaml | kubectl apply -f -

Write-Host "==> PVC + Grafana"
kubectl apply -f k8s/observability/historial-pvc.yml
kubectl apply -f k8s/observability/grafana.yml

Write-Host "==> Backend con historial (reconstruye imagen antes si cambiaste codigo)"
kubectl apply -f k8s/backend.yml

Write-Host ""
Write-Host "Siguiente: reconstruir backend y reiniciar"
Write-Host "  cd backend"
Write-Host "  docker build -t tfg-backend:v1 ."
Write-Host "  kubectl rollout restart deployment/backend-deployment"
Write-Host ""
Write-Host "Grafana: http://localhost:30002  (o kubectl port-forward svc/grafana 3000:3000)"
Write-Host "Login: admin / admin"
Write-Host "Espera 1-2 min la primera vez (plugin SQLite)."
