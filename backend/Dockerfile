FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# System deps needed for OCR fallback, image ops, and high-fidelity
# PDF -> DOCX conversion via LibreOffice's Writer PDF import filter.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr poppler-utils libgl1 \
      libreoffice-writer libreoffice-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install -U pip && pip install -e .

ENV PORT=8000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
