# Worker efímero por consulta (K8s)

## Arquitectura

```text
Streamlit (estático) → FastAPI orquestador (estático)
                           → Job Kubernetes (efímero, 1 por consulta)
                                → scrape + Ollama http://ollama:11434
                                → escribe /data/jobs/<id>/result.json
                           → lee resultado, guarda historial, responde
                           → Job se borra (ttlSecondsAfterFinished)
Ollama (estático)
```

## Despliegue

```powershell
cd TFG
kubectl apply -f k8s/observability/historial-pvc.yml
# Obligatorio antes del backend (permisos para crear Jobs)
kubectl apply -f k8s/backend-rbac.yml
kubectl get serviceaccount backend-job-runner
kubectl get rolebinding backend-job-runner

cd backend
docker build -t tfg-backend:v1 .
cd ..

kubectl apply -f k8s/ollama.yml
kubectl apply -f k8s/backend.yml
kubectl apply -f k8s/frontend.yml
```

## Comprobar modo

```powershell
kubectl port-forward svc/backend 8000:8000
```

http://localhost:8000/api/modo → `"worker_mode": "kubernetes"`

## Ver Jobs al generar un gráfico

```powershell
kubectl get jobs -l app=tfg-worker
kubectl get pods -l app=tfg-worker
```

## Docker Compose (sin Jobs)

En `docker-compose.yml` el backend usa por defecto `WORKER_MODE=sync` (comportamiento anterior).

## Variables

| Variable | Default | Uso |
|----------|---------|-----|
| WORKER_MODE | sync | `kubernetes` en K8s |
| JOB_TIMEOUT_SEC | 660 | Espera máxima del orquestador |
| WORKER_IMAGE | tfg-backend:v1 | Imagen del Job |
