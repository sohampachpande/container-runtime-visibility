FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY requirements-datastore.txt ./
COPY datastore_ui/requirements.txt ./datastore_ui/requirements.txt
RUN pip install --no-cache-dir -r datastore_ui/requirements.txt
COPY scripts/ingest_events.py ./scripts/ingest_events.py
COPY datastore_ui ./datastore_ui

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)"
CMD ["python", "-m", "streamlit", "run", "datastore_ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"]
