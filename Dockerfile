FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY importer.py server.py ./
COPY data/ data/
COPY static/ static/

EXPOSE 8000

# 2 workers are plenty; the plan cache is per-worker but refreshes cheaply
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "server:app"]
