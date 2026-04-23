from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .engaging import engaging_from_raw
from .extractor import extract_pdf, result_to_raw_markdown
from .models import EngagingRequest, EngagingResponse, ExtractionResult

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pdf-extractor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("pdf-extractor backend starting up")
    yield


app = FastAPI(
    title="PDF Data Extraction & Engaging Mode API",
    description=(
        "Extracts text, tables, images, annotations, and metadata from PDFs "
        "with a strict anti-hallucination policy. Offers an optional Engaging "
        "Mode that reformats (but never alters) the extracted content."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — permissive by default so the Next.js frontend (any origin) can call.
allowed = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed.split(",")] if allowed != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractResponse(BaseModel):
    result: ExtractionResult
    raw_markdown: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> dict[str, object]:
    return {
        "name": "pdf-extractor-backend",
        "endpoints": [
            "POST /extract  (multipart: file=<pdf>)",
            "POST /engaging (json: {raw_markdown})",
            "POST /download (json: {content, format, filename})",
            "GET  /healthz",
        ],
    }


@app.post("/extract", response_model=ExtractResponse)
async def extract(file: UploadFile = File(...)) -> ExtractResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a .pdf file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    # Sanity: PDFs start with %PDF-
    if not data.lstrip().startswith(b"%PDF"):
        raise HTTPException(400, "File does not appear to be a valid PDF")

    try:
        result = extract_pdf(data, file.filename)
    except Exception as exc:
        log.exception("extraction failed")
        raise HTTPException(500, f"Extraction failed: {exc}") from exc

    raw = result_to_raw_markdown(result)
    # Refresh sha256 to cover the full Markdown output.
    result.sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ExtractResponse(result=result, raw_markdown=raw)


@app.post("/engaging", response_model=EngagingResponse)
def engaging(req: EngagingRequest) -> EngagingResponse:
    if not req.raw_markdown.strip():
        raise HTTPException(400, "raw_markdown is empty")
    try:
        out = engaging_from_raw(req.raw_markdown, req.model)
    except Exception as exc:
        log.exception("engaging mode failed")
        raise HTTPException(500, f"Engaging mode failed: {exc}") from exc
    return EngagingResponse(
        engaging_markdown=out.engaging_markdown,
        provider=out.provider,
        model=out.model,
        sha256_input=hashlib.sha256(req.raw_markdown.encode("utf-8")).hexdigest(),
    )


class DownloadRequest(BaseModel):
    content: str
    format: Literal["md", "html", "txt"] = "md"
    filename: str = "extracted"


@app.post("/download")
def download(req: DownloadRequest) -> Response:
    fmt = req.format
    fname = req.filename.rsplit(".", 1)[0] or "extracted"
    if fmt == "md":
        body = req.content.encode("utf-8")
        media = "text/markdown; charset=utf-8"
        name = f"{fname}.md"
    elif fmt == "txt":
        body = req.content.encode("utf-8")
        media = "text/plain; charset=utf-8"
        name = f"{fname}.txt"
    else:  # html
        html = _md_to_html_document(req.content, fname)
        body = html.encode("utf-8")
        media = "text/html; charset=utf-8"
        name = f"{fname}.html"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _md_to_html_document(md: str, title: str) -> str:
    # very small, safe md->html: we embed the markdown verbatim inside <pre>
    # plus a tiny client-side renderer via marked.js from a CDN so the HTML
    # file renders nicely but still contains the original MD for round-tripping.
    import html

    escaped = html.escape(md)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#111} "
        "table{border-collapse:collapse;margin:1em 0} td,th{border:1px solid #ccc;padding:.35em .6em} "
        "blockquote{border-left:4px solid #f5b400;background:#fff9e6;margin:1em 0;padding:.5em 1em;border-radius:4px} "
        "code,pre{background:#f6f8fa;border-radius:4px;padding:.15em .4em} pre{padding:.75em;overflow:auto}</style>"
        "<script src='https://cdn.jsdelivr.net/npm/marked/marked.min.js'></script>"
        "</head><body>"
        f"<div id='md' data-src='{escaped}'></div>"
        "<script>document.getElementById('md').innerHTML="
        "marked.parse(document.getElementById('md').dataset.src);</script>"
        "</body></html>"
    )
