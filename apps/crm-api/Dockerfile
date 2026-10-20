# syntax=docker/dockerfile:1
# TFSC Sales CRM v2: API + compiled web app in one image. Build context: repository root.
#   docker build -f apps/crm-api/Dockerfile -t tfsc-crm:2 .
# The same image runs the API (default), the outbox dispatcher and the scheduler (see infra/compose).
FROM node:22-bookworm-slim AS web
WORKDIR /src
COPY packages/refine/ts/src packages/refine/ts/src
COPY apps/crm-web/package.json apps/crm-web/package-lock.json apps/crm-web/.npmrc apps/crm-web/
# Optional: behind a TLS-inspecting proxy pass its CA as a build secret (not stored in the image):
#   docker build --secret id=build_ca,src=/path/to/ca.crt ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    cd apps/crm-web && npm ci --no-audit --no-fund
COPY apps/crm-web apps/crm-web
RUN cd apps/crm-web && npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CRM_WEB_DIST=/app/web/dist CRM_DATA_DIR=/data CRM_MODE=hosted
WORKDIR /app
COPY apps/crm-api/requirements.txt /app/requirements.txt
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY packages/refine/python /opt/packages/refine/python
COPY packages/bridge-contract /opt/packages/bridge-contract
ENV TFSC_BRIDGE_SCHEMAS=/opt/packages/bridge-contract/schemas
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir /opt/packages/refine/python /opt/packages/bridge-contract/python
COPY apps/crm-api/ /app
COPY --from=web /src/apps/crm-web/dist /app/web/dist
RUN useradd --create-home --uid 10002 crm && mkdir -p /data && chown -R crm:crm /app /data
USER crm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os,urllib.request,urllib.parse; r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'Host':urllib.parse.urlsplit(os.environ['CRM_ORIGIN']).netloc}); urllib.request.urlopen(r,timeout=3)" || exit 1
CMD ["uvicorn", "tfsc_crm.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]
