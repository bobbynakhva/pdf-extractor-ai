# syntax=docker/dockerfile:1.7

# ---- Stage 1: build the Next.js static export ----
FROM node:20-bookworm-slim AS frontend
WORKDIR /fe
# package.json / lockfile first for better layer caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
# The frontend is same-origin in production — leave NEXT_PUBLIC_API_BASE empty
# so it calls `/extract`, `/render/...`, etc. relative to the page it was
# served from. That means you can expose this container on any host/port
# without rebuilding the frontend.
ENV NEXT_PUBLIC_API_BASE=""
RUN npm run build


# ---- Stage 2: the FastAPI backend + baked-in frontend ----
FROM python:3.12-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# System deps: tesseract/poppler for OCR on scanned PDFs; libreoffice is
# used by the "Word (layout)" export for pixel-perfect PDF->DOCX.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr poppler-utils libgl1 \
      libreoffice-writer libreoffice-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install -U pip && pip install -e .

# Copy the frontend static export from the build stage so the backend can
# serve it at `/`.
COPY --from=frontend /fe/out ./frontend_static

ENV PORT=8000 FRONTEND_DIR=/app/frontend_static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
