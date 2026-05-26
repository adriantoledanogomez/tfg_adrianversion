# Probar en la VM con `qwen2.5:7b`

Configuración por defecto en `k8s/backend.yml` y `k8s/frontend.yml`.

## 1. Actualizar código en la VM

```bash
cd ~/tfg_adrianversion
git pull
```

## 2. Descargar el modelo en Ollama (solo la primera vez)

```bash
kubectl wait --for=condition=ready pod -l app=ollama --timeout=300s

kubectl exec deploy/ollama-deployment -c ollama -- ollama pull qwen2.5:7b
kubectl exec deploy/ollama-deployment -c ollama -- ollama list
```

Debe aparecer `qwen2.5:7b`.

## 3. Rebuild backend (incluye reparación de JSON) e importar en K3s

```bash
docker build -t tfg-backend:v1 ./backend
docker build -t tfg-frontend:v1 ./frontend
docker save tfg-backend:v1 | sudo k3s ctr images import -
docker save tfg-frontend:v1 | sudo k3s ctr images import -
```

## 4. Aplicar manifiestos y reiniciar

```bash
kubectl apply -f k8s/ollama-pvc.yml
kubectl apply -f k8s/ollama.yml
kubectl apply -f k8s/backend-rbac.yml
kubectl apply -f k8s/backend.yml
kubectl apply -f k8s/frontend.yml

kubectl rollout restart deploy/backend-deployment deploy/frontend-deployment
kubectl get pods
```

## 5. Comprobar antes de usar Streamlit

El nombre `backend` **solo funciona dentro del clúster**, no desde SSH en la VM.
`curl http://backend:8000/...` en la shell de la VM dará `Could not resolve host`.

**Opción A — pod temporal (recomendado):**

```bash
kubectl run curl-test --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s http://backend:8000/api/ollama/estado
```

**Opción B — port-forward (prueba desde la VM con localhost):**

```bash
kubectl port-forward svc/backend 8000:8000
# En otra terminal SSH:
curl -s http://127.0.0.1:8000/api/ollama/estado
```

`modelo_disponible` debe ser `true` para `qwen2.5:7b`.

## 6. Probar en el navegador

`http://<IP_VM>:30001`

- Primera consulta tras el pull: **puede tardar 15–40 min** en CPU.
- Prompt ejemplo: *«Máximo 10 CCAA: población en millones. Solo cifras del texto.»*
- URL con tabla clara (Wikipedia demografía, INE, etc.).

## Modo rápido (opcional)

En `k8s/backend.yml` cambia `OLLAMA_MODEL` a `llama3.2:3b`, baja `OLLAMA_TIMEOUT_SEC` a `1800` y `JOB_TIMEOUT_SEC` a `2100`, luego `kubectl apply` + `ollama pull llama3.2:3b`.
