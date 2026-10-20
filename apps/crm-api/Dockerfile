FROM node:22-bookworm-slim AS frontend
WORKDIR /build
# Build context: apps/ (see infra/compose)
COPY crm-web/package.json crm-web/package-lock.json ./
RUN npm ci
COPY crm-web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY crm-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY crm-api/ /app
COPY --from=frontend /build/dist /app/web/dist
RUN useradd --create-home --uid 10002 crm && mkdir -p /data && chown -R crm:crm /app /data
USER crm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os,urllib.request,urllib.parse; r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'Host':urllib.parse.urlsplit(os.environ['CRM_ORIGIN']).netloc}); urllib.request.urlopen(r,timeout=3)" || exit 1
CMD ["uvicorn", "crm_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
