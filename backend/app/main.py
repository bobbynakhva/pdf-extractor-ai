from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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


@app.get("/api")
def api_index() -> dict[str, object]:
    return {
        "name": "pdf-extractor-backend",
        "endpoints": [
            "POST /extract  (multipart: file=<pdf>)",
            "POST /engaging (json: {raw_markdown})",
            "POST /download (json: {content, format, filename})",
            "GET  /render/{pdf_sha256}/{page}  (?dpi=N)",
            "GET  /export-docx/{pdf_sha256}  (?filename=name&engine=editable|layout)  — sync for small PDFs, 202+job for large",
            "POST /export-docx/{pdf_sha256}/jobs  (?engine=editable|layout)  — start background conversion",
            "GET  /export-docx/jobs/{job_id}  — poll progress",
            "GET  /export-docx/jobs/{job_id}/result  (?filename=name)  — download when state='done'",
            "GET  /healthz",
        ],
    }


# --- In-memory PDF bytes cache for the lazy /render endpoint ---
# Keyed by sha256(pdf_bytes). Entries expire after RENDER_CACHE_TTL seconds
# and the cache is capped at RENDER_CACHE_MAX entries (oldest evicted).
# We keep the raw PDF bytes (not the rendered PNGs) so memory stays flat
# regardless of how many pages the document has.
_CACHE_TTL = int(os.getenv("RENDER_CACHE_TTL", "1800"))  # 30 min
_CACHE_MAX = int(os.getenv("RENDER_CACHE_MAX", "16"))
_DEFAULT_DPI = int(os.getenv("PDF_RENDER_DPI", "150"))
_pdf_cache: "OrderedDict[str, tuple[bytes, float]]" = OrderedDict()
_cache_lock = threading.Lock()
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _cache_put(sha: str, data: bytes) -> None:
    now = time.time()
    with _cache_lock:
        _pdf_cache[sha] = (data, now)
        _pdf_cache.move_to_end(sha)
        # Evict by size.
        while len(_pdf_cache) > _CACHE_MAX:
            _pdf_cache.popitem(last=False)
        # Evict by age.
        for k in list(_pdf_cache.keys()):
            if now - _pdf_cache[k][1] > _CACHE_TTL:
                _pdf_cache.pop(k, None)


def _cache_get(sha: str) -> bytes | None:
    with _cache_lock:
        entry = _pdf_cache.get(sha)
        if not entry:
            return None
        data, ts = entry
        if time.time() - ts > _CACHE_TTL:
            _pdf_cache.pop(sha, None)
            return None
        _pdf_cache.move_to_end(sha)
        return data


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

    pdf_sha = hashlib.sha256(data).hexdigest()

    try:
        result = extract_pdf(data, file.filename)
    except Exception as exc:
        log.exception("extraction failed")
        raise HTTPException(500, f"Extraction failed: {exc}") from exc

    # Cache PDF bytes for the lazy /render endpoint.
    _cache_put(pdf_sha, data)
    result.pdf_sha256 = pdf_sha
    for page in result.pages:
        page.render_png = f"/render/{pdf_sha}/{page.number}"

    raw = result_to_raw_markdown(result)
    # Refresh sha256 to cover the full Markdown output (distinct from pdf_sha256).
    result.sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ExtractResponse(result=result, raw_markdown=raw)


@app.get("/render/{pdf_sha256}/{page}")
def render_page(pdf_sha256: str, page: int, dpi: int | None = None) -> Response:
    """Render a single page of a previously-extracted PDF to PNG on demand.

    The PDF bytes were cached during /extract keyed by their SHA-256. This
    endpoint is stateless from the client's perspective — the frontend just
    points `<img src>` at it and the browser caches the response.
    """
    if not _SHA_RE.match(pdf_sha256):
        raise HTTPException(400, "invalid pdf_sha256")
    if page < 1:
        raise HTTPException(400, "page must be >= 1")
    use_dpi = dpi or _DEFAULT_DPI
    if use_dpi < 36 or use_dpi > 400:
        raise HTTPException(400, "dpi must be between 36 and 400")

    data = _cache_get(pdf_sha256)
    if data is None:
        raise HTTPException(
            404, "PDF not cached; re-upload via /extract to refresh the cache"
        )

    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise HTTPException(500, f"PyMuPDF unavailable: {exc}")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise HTTPException(400, f"Failed to open cached PDF: {exc}")
    try:
        if page > doc.page_count:
            raise HTTPException(
                404, f"page {page} out of range (document has {doc.page_count})"
            )
        pix = doc.load_page(page - 1).get_pixmap(dpi=use_dpi, alpha=False)
        png_bytes = pix.tobytes("png")
    finally:
        doc.close()

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # Cache aggressively — the PDF bytes are immutable (sha in URL)
            # and the DPI is part of the URL's query string.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


def _convert_pdf_to_docx_libreoffice(pdf_bytes: bytes) -> bytes | None:
    """Convert PDF -> DOCX using LibreOffice's PDF import filter.

    Returns the DOCX bytes on success, or None if LibreOffice is not
    installed or the conversion fails. This path preserves layout, tables,
    and images much more faithfully than `pdf2docx` does, so we prefer it
    whenever the binary is available.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pdf_path = tmp / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)
        # `writer_pdf_import` lets Writer open the PDF as editable content,
        # then we re-save as docx. `--headless` + isolated UserProfile
        # avoids per-user lock collisions if multiple requests overlap.
        user_profile = (tmp / "lo_profile").as_uri()
        try:
            proc = subprocess.run(
                [
                    soffice,
                    "--headless",
                    f"-env:UserInstallation={user_profile}",
                    "--infilter=writer_pdf_import",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(tmp),
                    str(pdf_path),
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except Exception as exc:
            log.warning("libreoffice invocation failed: %s", exc)
            return None
        if proc.returncode != 0:
            log.warning(
                "libreoffice returned %s: %s",
                proc.returncode,
                proc.stderr.decode("utf-8", errors="replace")[:500],
            )
            return None
        out_docx = tmp / "input.docx"
        if not out_docx.exists():
            return None
        return out_docx.read_bytes()


def _convert_pdf_to_docx_pdf2docx(pdf_bytes: bytes) -> bytes:
    """Fallback pure-Python PDF -> DOCX via `pdf2docx`."""
    import tempfile
    from pathlib import Path

    from pdf2docx import Converter  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "input.pdf"
        docx_path = Path(tmpdir) / "output.docx"
        pdf_path.write_bytes(pdf_bytes)
        cv = Converter(str(pdf_path))
        # `multi_processing=False` keeps memory under control on small VMs.
        cv.convert(str(docx_path), start=0, end=None, multi_processing=False)
        cv.close()
        return docx_path.read_bytes()


# ---------- DOCX export: background-job pipeline ----------
#
# Large PDFs take 30+ seconds (up to several minutes) to convert via
# pdf2docx, which is well beyond what browsers, proxies and tunnels are
# willing to hold open on a single HTTP request. We run the actual
# conversion on a background worker thread and expose three endpoints:
#
#     POST /export-docx/{sha}/jobs?engine=...   -> {job_id, state}
#     GET  /export-docx/jobs/{job_id}           -> progress snapshot
#     GET  /export-docx/jobs/{job_id}/result    -> 200 docx when ready,
#                                                  202 while still running
#
# The legacy `GET /export-docx/{sha}` handler below synchronously blocks
# for small PDFs (<= _DOCX_SYNC_PAGE_LIMIT pages) and otherwise delegates
# to the job path so short docs still download in one shot.

_DOCX_JOBS_MAX = int(os.getenv("DOCX_JOBS_MAX", "32"))
_DOCX_JOBS_TTL = int(os.getenv("DOCX_JOBS_TTL", "3600"))  # 1 hour
_DOCX_SYNC_PAGE_LIMIT = int(os.getenv("DOCX_SYNC_PAGE_LIMIT", "10"))


class _DocxJob:
    __slots__ = (
        "job_id",
        "sha",
        "engine",
        "state",
        "progress",
        "total",
        "phase",
        "result_bytes",
        "result_engine",
        "error",
        "created_at",
        "finished_at",
        "thread",
    )

    def __init__(self, job_id: str, sha: str, engine: str) -> None:
        self.job_id = job_id
        self.sha = sha
        self.engine = engine
        self.state = "pending"
        self.progress = 0
        self.total = 0
        self.phase = "queued"
        self.result_bytes: bytes | None = None
        self.result_engine = ""
        self.error: str | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.thread: threading.Thread | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "engine": self.engine,
            "phase": self.phase,
            "progress": self.progress,
            "total": self.total,
            "result_engine": self.result_engine,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


_docx_jobs: "OrderedDict[str, _DocxJob]" = OrderedDict()
_docx_jobs_by_key: dict[tuple[str, str], str] = {}
_docx_jobs_lock = threading.Lock()


def _docx_jobs_purge_locked() -> None:
    """Evict finished jobs older than TTL, then cap size to MAX."""
    now = time.time()
    stale = [
        jid
        for jid, job in _docx_jobs.items()
        if job.finished_at is not None and now - job.finished_at > _DOCX_JOBS_TTL
    ]
    for jid in stale:
        job = _docx_jobs.pop(jid, None)
        if job is not None:
            _docx_jobs_by_key.pop((job.sha, job.engine), None)
    while len(_docx_jobs) > _DOCX_JOBS_MAX:
        jid, job = _docx_jobs.popitem(last=False)
        _docx_jobs_by_key.pop((job.sha, job.engine), None)


def _install_pdf2docx_progress(job: _DocxJob) -> logging.Handler:
    """Pipe pdf2docx's per-page log messages into the job's progress field.

    pdf2docx prints e.g. ``(23/60) Page 23`` for both its parsing and
    writing phases. We intercept the logger so the client can poll for
    smooth progress during long conversions.
    """
    _PAGE_RE = re.compile(r"\((\d+)/(\d+)\) Page")

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = record.getMessage()
            except Exception:
                return
            m = _PAGE_RE.search(msg)
            if m:
                job.progress = int(m.group(1))
                job.total = int(m.group(2))
                return
            if "Analyzing document" in msg:
                job.phase = "analyzing"
            elif "Parsing pages" in msg:
                job.phase = "parsing"
            elif "Creating pages" in msg:
                job.phase = "writing"

    h = _Handler(level=logging.INFO)
    # pdf2docx uses ``logging.info(...)`` (root logger), so attach there.
    # The handler only reacts to messages matching the page-progress regex
    # or the known phase markers, so unrelated log records are ignored.
    root = logging.getLogger()
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    root.addHandler(h)
    return h


def _docx_worker(job: _DocxJob, pdf_bytes: bytes) -> None:
    job.state = "running"
    job.phase = "starting"
    handler: logging.Handler | None = None
    try:
        if job.engine == "layout":
            body = _convert_pdf_to_docx_libreoffice(pdf_bytes)
            used = "libreoffice"
            if body is None:
                # Graceful fallback to editable path if LibreOffice missing.
                job.phase = "falling-back-to-pdf2docx"
                handler = _install_pdf2docx_progress(job)
                body = _convert_pdf_to_docx_pdf2docx(pdf_bytes)
                used = "pdf2docx (libreoffice unavailable)"
        else:
            handler = _install_pdf2docx_progress(job)
            body = _convert_pdf_to_docx_pdf2docx(pdf_bytes)
            used = "pdf2docx"
        job.result_bytes = body
        job.result_engine = used
        job.state = "done"
        job.phase = "done"
        if job.total == 0:
            job.total = 1
        job.progress = job.total
    except Exception as exc:  # noqa: BLE001 — surface all errors to the client
        log.exception("pdf->docx job failed")
        job.state = "error"
        job.phase = "error"
        job.error = str(exc)
    finally:
        job.finished_at = time.time()
        if handler is not None:
            logging.getLogger().removeHandler(handler)


def _start_docx_job(pdf_sha256: str, engine: str) -> _DocxJob:
    data = _cache_get(pdf_sha256)
    if data is None:
        raise HTTPException(
            404, "PDF not cached; re-upload via /extract to refresh the cache"
        )
    key = (pdf_sha256, engine)
    with _docx_jobs_lock:
        _docx_jobs_purge_locked()
        # Reuse an existing not-yet-expired job for the same (sha, engine),
        # so repeated clicks don't kick off parallel conversions.
        existing_jid = _docx_jobs_by_key.get(key)
        if existing_jid is not None:
            existing = _docx_jobs.get(existing_jid)
            if existing is not None and existing.state != "error":
                _docx_jobs.move_to_end(existing_jid)
                return existing
        job_id = hashlib.sha256(
            f"{pdf_sha256}:{engine}:{time.time()}:{os.urandom(6).hex()}".encode()
        ).hexdigest()[:24]
        job = _DocxJob(job_id, pdf_sha256, engine)
        _docx_jobs[job_id] = job
        _docx_jobs_by_key[key] = job_id
    t = threading.Thread(
        target=_docx_worker, args=(job, data), name=f"docx-{job_id}", daemon=True
    )
    job.thread = t
    t.start()
    return job


@app.post("/export-docx/{pdf_sha256}/jobs")
def export_docx_start(
    pdf_sha256: str, engine: str | None = None
) -> dict[str, object]:
    """Start a background PDF -> DOCX conversion. Returns a job_id."""
    if not _SHA_RE.match(pdf_sha256):
        raise HTTPException(400, "invalid pdf_sha256")
    mode = (engine or "editable").lower()
    if mode not in {"editable", "layout"}:
        raise HTTPException(400, "engine must be 'editable' or 'layout'")
    job = _start_docx_job(pdf_sha256, mode)
    return job.as_dict()


@app.get("/export-docx/jobs/{job_id}")
def export_docx_status(job_id: str) -> dict[str, object]:
    """Poll the status/progress of a conversion job."""
    with _docx_jobs_lock:
        job = _docx_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job_id (or expired)")
    return job.as_dict()


@app.get("/export-docx/jobs/{job_id}/result")
def export_docx_result(job_id: str, filename: str | None = None) -> Response:
    """Download the converted docx once a job has finished."""
    with _docx_jobs_lock:
        job = _docx_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job_id (or expired)")
    if job.state == "error":
        raise HTTPException(500, job.error or "conversion failed")
    if job.state != "done" or job.result_bytes is None:
        # 202 Accepted: still running. Client should keep polling
        # /export-docx/jobs/{job_id}.
        return JSONResponse(job.as_dict(), status_code=202)

    safe = (filename or "extracted").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "extracted"
    return Response(
        content=job.result_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{safe}.docx"',
            "Cache-Control": "public, max-age=3600",
            "X-Docx-Engine": job.result_engine,
        },
    )


@app.get("/export-docx/{pdf_sha256}")
def export_docx(
    pdf_sha256: str,
    filename: str | None = None,
    engine: str | None = None,
) -> Response:
    """Convert a previously-extracted PDF to a Word (.docx) document.

    Back-compat synchronous endpoint. Small documents (<=
    ``DOCX_SYNC_PAGE_LIMIT`` pages) stream the result inline so simple
    clients don't need to implement polling. Larger documents kick off
    the background-job path and return a ``202`` with the ``job_id`` so
    the client can poll ``/export-docx/jobs/{job_id}`` — this avoids the
    60-second browser/proxy timeouts we were hitting for 50+-page PDFs.

    Two engines are available:

    * ``engine=editable`` (default): uses ``pdf2docx`` to produce a
      document with flowing paragraphs and real Word tables. Content
      is fully selectable and editable in Word or Google Docs, with
      fonts, bold/italic, colors and images preserved.
    * ``engine=layout``: uses LibreOffice's ``writer_pdf_import`` to
      preserve the original visual layout pixel-for-pixel. Text lives
      inside drawing frames, so the layout matches but the content is
      not freely editable.
    """
    if not _SHA_RE.match(pdf_sha256):
        raise HTTPException(400, "invalid pdf_sha256")
    data = _cache_get(pdf_sha256)
    if data is None:
        raise HTTPException(
            404, "PDF not cached; re-upload via /extract to refresh the cache"
        )

    mode = (engine or "editable").lower()
    if mode not in {"editable", "layout"}:
        raise HTTPException(400, "engine must be 'editable' or 'layout'")

    # Peek at the page count to decide whether to stream synchronously.
    try:
        import fitz  # type: ignore

        with fitz.open(stream=data, filetype="pdf") as doc:
            page_count = doc.page_count
    except Exception:
        page_count = 0

    if page_count and page_count > _DOCX_SYNC_PAGE_LIMIT:
        job = _start_docx_job(pdf_sha256, mode)
        return JSONResponse(
            {
                **job.as_dict(),
                "note": (
                    "PDF too large for synchronous conversion — converting in "
                    "background. Poll /export-docx/jobs/{job_id} and download "
                    "from /export-docx/jobs/{job_id}/result when state='done'."
                ),
            },
            status_code=202,
        )

    body: bytes | None = None
    used = ""
    if mode == "layout":
        body = _convert_pdf_to_docx_libreoffice(data)
        used = "libreoffice"
        if body is None:
            try:
                body = _convert_pdf_to_docx_pdf2docx(data)
                used = "pdf2docx (libreoffice unavailable)"
            except Exception as exc:
                log.exception("pdf->docx conversion failed")
                raise HTTPException(500, f"pdf->docx conversion failed: {exc}") from exc
    else:
        try:
            body = _convert_pdf_to_docx_pdf2docx(data)
            used = "pdf2docx"
        except Exception as exc:
            log.exception("pdf->docx conversion failed")
            raise HTTPException(500, f"pdf->docx conversion failed: {exc}") from exc

    safe = (filename or "extracted").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "extracted"
    return Response(
        content=body,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{safe}.docx"',
            "Cache-Control": "public, max-age=3600",
            "X-Docx-Engine": used,
        },
    )


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


# --- Single-origin static hosting ---
# If FRONTEND_DIR points at a built Next.js static export (`frontend/out`),
# serve it at the root so the backend + frontend share one URL. This is how
# the Docker image ships — the multi-stage build bakes the static files in,
# and users can expose just one port (`/` serves the UI, `/extract` etc.
# serve the API). Falls back to a plain "API only" landing page when no
# frontend is present (e.g. when running the backend directly for dev).
_FRONTEND_DIR = os.getenv(
    "FRONTEND_DIR", os.path.join(os.path.dirname(__file__), "..", "frontend_static")
)
if os.path.isdir(_FRONTEND_DIR):
    # `html=True` serves index.html for `/` and 404s (SPA-style).
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
    log.info("serving frontend static export from %s", _FRONTEND_DIR)
else:

    @app.get("/", include_in_schema=False)
    def _root_placeholder() -> dict[str, str]:
        return {
            "name": "pdf-extractor-backend",
            "note": "No frontend bundle found. API is at /api.",
        }
