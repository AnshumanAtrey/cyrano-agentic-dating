#!/bin/sh
# First boot on a fresh volume: copy the finished demo run in, then serve.
mkdir -p /app/data
[ -f /app/data/app.db ] || cp /app/seed/app.db /app/data/app.db
[ -d /app/data/photos ] || cp -R /app/seed/photos /app/data/photos
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
