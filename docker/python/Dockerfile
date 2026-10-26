FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        gcc \
        g++ \
        gdal-bin \
        python3-gdal \
        libgdal-dev \
        proj-bin \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker/python/requirements.txt /app/requirements.txt
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY app/ /app/
COPY data/ /data/
