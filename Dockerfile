# LeadHub — runs the stdlib server on an always-on host (Render/Railway/Fly/VPS).
FROM python:3.12-slim

# Non-root user; data lives on a mounted volume outside the code tree.
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY leadhub ./leadhub
COPY app.py ./

ENV LEADHUB_DATA_DIR=/data \
    LEADHUB_BIND=0.0.0.0 \
    LEADHUB_PORT=8000 \
    LEADHUB_ENFORCE_HTTPS=1 \
    LEADHUB_ADMIN_PIN_MIN=6 \
    PYTHONUNBUFFERED=1

# The host may inject $PORT; config.py prefers it over LEADHUB_PORT.
EXPOSE 8000
VOLUME ["/data"]
USER app
CMD ["python", "app.py"]
