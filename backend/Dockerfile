FROM python:3.10-slim
WORKDIR /app
RUN mkdir -p /data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend.py historial.py pipeline.py worker.py k8s_jobs.py .
ENV HISTORY_ENABLED=0
ENV HISTORY_DB_PATH=/data/historial.db
EXPOSE 8000
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
