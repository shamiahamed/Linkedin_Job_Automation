FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Tesseract for image OCR (used by /api/ocr/from-image + paste capture images)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --timeout 120 main:app"]