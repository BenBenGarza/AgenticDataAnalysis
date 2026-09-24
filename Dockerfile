FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them on rebuild.
COPY requirements.txt .
RUN pip install -r requirements.txt

# The app, plus docs/ (the system prompt is built from it at startup).
COPY app ./app
COPY docs ./docs

# Run as an unprivileged user; conversations are stored in /app/data (a volume).
RUN useradd --create-home --uid 1000 appuser && mkdir data && chown appuser:appuser data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8000"]
