# TFSC Agreements portal. Build context: repository root (it needs packages/bridge-contract).
#   docker build -f apps/agreements/Dockerfile -t tfsc-agreements:4 .
# Private signature images are never part of the image (.dockerignore); install them on the server
# with `python manage.py install-signatures`.
FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TFSC_BRIDGE_SCHEMAS=/opt/packages/bridge-contract/schemas
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation2 fontconfig && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/agreements/requirements.txt ./
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY packages/bridge-contract /opt/packages/bridge-contract
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir /opt/packages/bridge-contract/python
COPY apps/agreements/ /app
RUN useradd -m -u 10001 tfsc && mkdir -p /data /contracts && chown -R tfsc:tfsc /app /data /contracts
USER tfsc
EXPOSE 8000
CMD ["uvicorn","portal:app","--host","0.0.0.0","--port","8000","--workers","2","--no-access-log"]
