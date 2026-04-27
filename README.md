# 📄 PDF Extractor AI

Web app that extracts **every piece of information** from any PDF — text, tables, images, highlights, annotations, metadata — with **zero hallucination** and **zero data loss**, plus a pixel-perfect PDF Layout view, Markdown export, and editable Word (`.docx`) export.

- **Backend:** FastAPI + PyMuPDF + pdfplumber + pypdf (+ Tesseract OCR fallback)
- **Frontend:** Next.js 14 (App Router) + TailwindCSS + `marked` + DOMPurify
- **No outbound LLM dependency.** Everything runs locally; no API keys required.

## Highlights

- ✅ **Fallback chain** — PyMuPDF → pdfplumber → pypdf. OCR (Tesseract) auto-runs on pages with no embedded text.
- ✅ **Never fabricates** — if a block can't be read, the output contains an explicit `⚠️ EXTRACTION FLAG` rather than invented content.
- ✅ **Tables** as proper Markdown tables, with low-confidence tables flagged.
- ✅ **Images** embedded as base64 data URLs (original resolution preserved).
- ✅ **Highlights / underlines / strikeout / sticky notes** parsed with colors and the underlying text.
- ✅ **SHA-256 integrity hash** of the extracted Markdown.
- ✅ **PDF Layout view** — pixel-perfect raster rendering with highlight overlays.
- ✅ **Raw MD view** — copy/paste-ready Markdown.
- ✅ **Word export** — editable (`pdf2docx`) or pixel-perfect layout (`libreoffice`), with a background-job pipeline for large PDFs.
- ✅ **Side-by-side** original PDF + extracted content view.
- ✅ **Search / Copy-all / Download (MD/HTML/TXT) / Zoom / Dark mode.**
- ✅ **Any language** — UTF-8 throughout.

## Repository layout

```
backend/               FastAPI service
  app/
    extractor.py       multi-engine extraction + OCR + anti-hallucination flags
    main.py            /extract, /render, /export-docx, /download, /healthz
    models.py          pydantic schemas
  tests/make_sample_pdf.py    generates a sample PDF with text, table, image, highlight
frontend/              Next.js 14 app (App Router)
  src/app/page.tsx     upload, preview, layout/raw toggle, search, copy, download
```

## Run it yourself (Docker — easiest)

One command gets you a fully working app at `http://localhost:8000`. The backend serves the frontend, so there's just one URL. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/bobbynakhva/pdf-extractor-ai.git
cd pdf-extractor-ai
make setup   # copies .env.example -> .env and builds the image
make run     # starts the container; open http://localhost:8000
```

Other targets: `make logs` / `make stop` / `make restart` / `make rebuild` / `make clean`. Run `make help` for the full list.

### Expose it to the public internet (free)

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) gives you a free public HTTPS URL for your local app — no port forwarding, no dynamic DNS, nothing to manage.

```bash
# install cloudflared first (only once)
brew install cloudflared             # macOS
# or apt/rpm: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

make tunnel
# prints:  https://<random>.trycloudflare.com  ← your public URL
```

For a permanent URL on your own domain, run `cloudflared tunnel login`, create a named tunnel, and route your subdomain at it — free with any domain you have on Cloudflare.

## Running without Docker (dev mode)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Requires system packages: `tesseract-ocr`, `poppler-utils`, and (for high-fidelity "Word (layout)" export) `libreoffice-writer` + `libreoffice-core`.

### Frontend

```bash
cd frontend
npm install
# Point at your backend (default is http://localhost:8000 when frontend runs on localhost):
# export NEXT_PUBLIC_API_BASE=https://your-backend.example.com
npm run dev     # or: npm run build && npm start
```

Open http://localhost:3000.

## API

### `POST /extract`
multipart form, field name `file`. Returns:

```jsonc
{
  "result": {
    "filename": "sample.pdf",
    "page_count": 2,
    "metadata": { ... },
    "pages": [ { "number": 1, "blocks": [...], "plain_text": "...", "used_ocr": false }, ... ],
    "tables": [ { "page": 2, "rows": [["Year","Q1", ...], ...], "confidence": 0.96, "flagged": false } ],
    "images": [ { "page": 1, "index": 0, "data_url": "data:image/png;base64,...", ... } ],
    "annotations": [ { "kind": "highlight", "color": "#ffff00", "text": "23.4%", "page": 1 } ],
    "engines_used": ["pymupdf", "pdfplumber"],
    "warnings": [],
    "sha256": "573380241b1a...",
    "pdf_sha256": "...",
    "overall_confidence": 0.97
  },
  "raw_markdown": "# sample.pdf\n\n---\n\n## Page 1\n..."
}
```

### `GET /render/{pdf_sha256}/{page}?dpi=N`
Lazily renders one page of a previously-uploaded PDF as a PNG (default DPI 150).

### `POST /export-docx/{pdf_sha256}/jobs?engine=editable|layout`
Starts a background DOCX conversion. Returns `{ job_id, state, phase, progress, total }`.

### `GET /export-docx/jobs/{job_id}`
Polls progress: `{ state: "queued"|"running"|"done"|"error", phase, progress, total, error? }`.

### `GET /export-docx/jobs/{job_id}/result?filename=name`
Returns 202 while running, 200 + DOCX bytes when state is `"done"`.

### `POST /download`
JSON `{ "content": "...", "format": "md"|"html"|"txt", "filename": "..." }`. Returns an attachment.

### `GET /healthz`

## Anti-hallucination safeguards

| Safeguard | Where |
|---|---|
| Fallback chain of 3 parsers + OCR | `backend/app/extractor.py` |
| Explicit `⚠️ EXTRACTION FLAG` blocks when a step fails | `PageBlock(kind="flag", ...)` |
| Per-block confidence scores | `PageBlock.confidence` |
| Low-confidence tables flagged for review | `ExtractedTable.flagged` |
| SHA-256 of raw Markdown surfaced in UI | `ExtractionResult.sha256` |
| pdf2docx subprocess + watchdog (no silent hangs) | `backend/app/main.py` |
| Extraction-based fallback DOCX builder when pdf2docx fails | `backend/app/main.py` |

## Roadmap (post-MVP)

- [ ] Camelot / Tabula for complex merged-cell tables
- [ ] Server-Sent Events for live render progress on huge PDFs
- [ ] Azure Document Intelligence as a premium OCR path
- [ ] Batch uploads + shareable expiring links
- [ ] True annotation-JSON export

## License

MIT
