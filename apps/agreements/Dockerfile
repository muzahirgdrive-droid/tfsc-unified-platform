FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation2 fontconfig && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN useradd -m -u 10001 tfsc && mkdir -p /data /contracts && chown -R tfsc:tfsc /app /data /contracts
USER tfsc
EXPOSE 8000
CMD ["uvicorn","portal:app","--host","0.0.0.0","--port","8000","--workers","2","--no-access-log"]
