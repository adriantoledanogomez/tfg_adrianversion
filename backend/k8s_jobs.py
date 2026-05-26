"""Jobs efímeros vía API REST de Kubernetes (token del ServiceAccount en /var/run/secrets/...)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests
from kubernetes import client
JOBS_ROOT = Path(os.getenv("JOBS_ROOT", "/data/jobs"))
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "default")
WORKER_IMAGE = os.getenv("WORKER_IMAGE", "tfg-backend:v1")
WORKER_IMAGE_PULL_POLICY = os.getenv("WORKER_IMAGE_PULL_POLICY", "Never")
JOB_TIMEOUT_SEC = int(os.getenv("JOB_TIMEOUT_SEC", "660"))
JOB_TTL_AFTER_FINISH = int(os.getenv("JOB_TTL_AFTER_FINISH", "300"))
PVC_NAME = os.getenv("HISTORIAL_PVC_NAME", "historial-pvc")

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
SA_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
SA_NAMESPACE_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


def _namespace_efectivo() -> str:
    if SA_NAMESPACE_PATH.is_file():
        return SA_NAMESPACE_PATH.read_text(encoding="utf-8").strip()
    return K8S_NAMESPACE


def _api_base() -> str:
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS") or os.getenv(
        "KUBERNETES_SERVICE_PORT", "443"
    )
    if isinstance(port, str) and ":" in port:
        port = port.split(":")[-1]
    return f"https://{host}:{port}"


def _headers() -> dict[str, str]:
    if not SA_TOKEN_PATH.is_file():
        raise RuntimeError("No hay token de ServiceAccount en el pod")
    token = SA_TOKEN_PATH.read_text(encoding="utf-8").strip()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _k8s_request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: int = 60,
) -> requests.Response:
    url = f"{_api_base()}{path}"
    verify = str(SA_CA_PATH) if SA_CA_PATH.is_file() else True
    return requests.request(
        method,
        url,
        headers=_headers(),
        params=params,
        json=body,
        verify=verify,
        timeout=timeout,
    )


def _job_to_dict(job: client.V1Job) -> dict[str, Any]:
    return client.ApiClient().sanitize_for_serialization(job)


def diagnostico_k8s_cliente() -> dict[str, Any]:
    ns = _namespace_efectivo()
    info: dict[str, Any] = {
        "kubernetes_service_host": os.getenv("KUBERNETES_SERVICE_HOST"),
        "token_presente": SA_TOKEN_PATH.is_file(),
        "token_longitud": (
            len(SA_TOKEN_PATH.read_text(encoding="utf-8").strip())
            if SA_TOKEN_PATH.is_file()
            else 0
        ),
        "namespace_archivo": ns,
        "namespace_usado": K8S_NAMESPACE,
        "api_autenticada": False,
        "puede_listar_jobs": False,
        "puede_crear_jobs": False,
        "metodo": "requests + bearer token",
    }

    try:
        r = _k8s_request(
            "GET",
            f"/apis/batch/v1/namespaces/{ns}/jobs",
            params={"limit": 1},
        )
        if r.status_code == 200:
            info["api_autenticada"] = True
            info["puede_listar_jobs"] = True
        else:
            info["error"] = r.text
            return info

        probe = _build_job_object("tfg-probe-dryrun", "probe00000001", "https://example.com", "test")
        r2 = _k8s_request(
            "POST",
            f"/apis/batch/v1/namespaces/{ns}/jobs",
            params={"dryRun": "All", "fieldManager": "tfg-backend"},
            body=_job_to_dict(probe),
        )
        if r2.status_code in (200, 201):
            info["puede_crear_jobs"] = True
        else:
            info["error_crear_dryrun"] = r2.text
    except Exception as exc:
        info["error"] = str(exc)

    return info


def _job_name(job_id: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "", job_id.lower())[:40]
    return f"tfg-worker-{safe}"


def _build_job_object(name: str, job_id: str, url: str, prompt: str) -> client.V1Job:
    ns = _namespace_efectivo()
    env = [
        client.V1EnvVar(name="JOB_ID", value=job_id),
        client.V1EnvVar(name="URL", value=url),
        client.V1EnvVar(name="PROMPT", value=prompt),
        client.V1EnvVar(
            name="OLLAMA_URL", value=os.getenv("OLLAMA_URL", "http://ollama:11434")
        ),
        client.V1EnvVar(
            name="OLLAMA_MODEL", value=os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        ),
        client.V1EnvVar(name="JOBS_ROOT", value=str(JOBS_ROOT)),
    ]
    container = client.V1Container(
        name="worker",
        image=WORKER_IMAGE,
        image_pull_policy=WORKER_IMAGE_PULL_POLICY,
        command=["python", "worker.py"],
        env=env,
        volume_mounts=[
            client.V1VolumeMount(name="historial-data", mount_path="/data")
        ],
    )
    pod_spec = client.V1PodSpec(
        restart_policy="Never",
        service_account_name="backend-job-runner",
        automount_service_account_token=True,
        containers=[container],
        volumes=[
            client.V1Volume(
                name="historial-data",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=PVC_NAME
                ),
            )
        ],
    )
    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=ns,
            labels={"app": "tfg-worker", "job-id": job_id},
        ),
        spec=client.V1JobSpec(
            template=client.V1PodTemplateSpec(spec=pod_spec),
            backoff_limit=0,
            ttl_seconds_after_finished=JOB_TTL_AFTER_FINISH,
        ),
    )


def crear_job_consulta(job_id: str, url: str, prompt: str) -> str:
    ns = _namespace_efectivo()
    name = _job_name(job_id)
    job = _build_job_object(name, job_id, url, prompt)

    r = _k8s_request(
        "POST",
        f"/apis/batch/v1/namespaces/{ns}/jobs",
        body=_job_to_dict(job),
        timeout=120,
    )
    if r.status_code == 409:
        return name
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"No se pudo crear el Job {name}: HTTP {r.status_code} — {r.text}"
        )
    return name


def _job_status(name: str) -> dict[str, Any]:
    ns = _namespace_efectivo()
    r = _k8s_request("GET", f"/apis/batch/v1/namespaces/{ns}/jobs/{name}/status")
    if r.status_code != 200:
        return {}
    return r.json()


def esperar_resultado_job(job_id: str, job_name: str) -> dict[str, Any]:
    result_path = JOBS_ROOT / job_id / "result.json"
    deadline = time.time() + JOB_TIMEOUT_SEC

    while time.time() < deadline:
        if result_path.is_file():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
                if data.get("status") in ("success", "error"):
                    return data
            except json.JSONDecodeError:
                pass

        st = _job_status(job_name)
        status = st.get("status") or {}
        if (status.get("failed") or 0) > 0:
            if result_path.is_file():
                return json.loads(result_path.read_text(encoding="utf-8"))
            raise RuntimeError(f"El Job {job_name} falló sin archivo de resultado")
        if (status.get("succeeded") or 0) > 0 and result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8"))

        time.sleep(2)

    raise TimeoutError(
        f"Tiempo de espera agotado ({JOB_TIMEOUT_SEC}s) para el Job {job_name}"
    )


def ejecutar_consulta_en_job(url: str, prompt: str) -> dict[str, Any]:
    import uuid

    job_id = uuid.uuid4().hex[:12]
    job_name = crear_job_consulta(job_id, url, prompt)
    resultado = esperar_resultado_job(job_id, job_name)
    resultado["k8s_job_name"] = job_name
    resultado.setdefault("job_id", job_id)
    return resultado
