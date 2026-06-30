FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        iproute2 \
        iputils-ping \
        net-tools \
        procps \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/objects
COPY environment/services/shared/mtd_common.py /app/mtd_common.py
COPY environment/services/cloud-object/app.py /app/app.py

VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "/app/app.py"]
