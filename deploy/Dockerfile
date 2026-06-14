# Muffin Manager container — runs the farm's brain on a NAS/server.
# Build from the repo root:
#   docker build -t muffin-manager -f deploy/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

# The manager only needs the API stack — no GUI, no DCCs, no psutil.
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "requests>=2.31"

COPY muffin /app/muffin

# State (SQLite db + settings) lives on a mounted volume.
ENV MUFFIN_DATA_DIR=/data \
    MUFFIN_HOST=0.0.0.0 \
    MUFFIN_PORT=8080

VOLUME /data
EXPOSE 8080

CMD ["python", "-m", "muffin.manager"]
