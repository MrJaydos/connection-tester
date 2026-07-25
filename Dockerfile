FROM python:3.12-alpine

# tzdata lets the TZ env var produce correct local timestamps in messages/logs.
RUN apk add --no-cache tzdata

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY monitor.py .

# Persisted outage state lives here; mount a volume to survive restarts.
RUN mkdir -p /data
VOLUME ["/data"]

ENTRYPOINT ["python", "/app/monitor.py"]
