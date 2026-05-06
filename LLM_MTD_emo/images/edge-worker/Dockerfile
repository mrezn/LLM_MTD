FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        curl \
        iproute2 \
        iputils-ping \
        net-tools \
        procps \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY images/shared/mtd_common.py /app/mtd_common.py
COPY images/edge-worker/app.py /app/app.py

EXPOSE 8000
CMD ["python", "/app/app.py"]
