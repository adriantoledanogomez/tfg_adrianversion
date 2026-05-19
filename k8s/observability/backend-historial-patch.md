# Parche opcional para el backend en K8s (historial)

No modifica automáticamente `backend.yml`. Añade a mano en `backend-deployment`:

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

Orden sugerido:

```powershell
kubectl apply -f k8s/observability/historial-pvc.yml
# (editar backend.yml con lo de arriba, rebuild imagen, rollout)
kubectl apply -f k8s/observability/grafana.yml
```

Grafana: http://localhost:30002 (NodePort) o `kubectl port-forward svc/grafana 3000:3000`

Importa el dashboard desde `observability/grafana/provisioning/dashboards/json/tfg-historial.json` si el ConfigMap del JSON no lo montaste aún.
