# 📄 PDF Extractor AI

Web app that extracts **every piece of information** from any PDF — text, tables, images, highlights, annotations, metadata — with **zero hallucination** and **zero data loss**, then offers an optional **Engaging Mode** that reformats the extracted content into a visually rich, human-friendly view **without altering a single word, number, or data point**.

- **Backend:** FastAPI + PyMuPDF + pdfplumber + pypdf (+ Tesseract OCR fallback)
- **Frontend:** Next.js 14 (App Router) + TailwindCSS + `marked` + DOMPurify
- **LLM (Engaging Mode):** CLōD (OpenAI-compatible gateway with 100 free requests/day), OpenAI, or Anthropic, with a strict format-only system prompt. Falls back to a deterministic rule-based formatter when no API key is configured so the feature still works end-to-end.

## Highlights

- ✅ **Fallback chain** — PyMuPDF → pdfplumber → pypdf. OCR (Tesseract) auto-runs on pages with no embedded text.
- ✅ **Never fabricates** — if a block can't be read, the output contains an explicit `⚠️ EXTRACTION FLAG` rather than invented content.
- ✅ **Tables** as proper Markdown tables, with low-confidence tables flagged.
- ✅ **Images** embedded as base64 data URLs (original resolution preserved).
- ✅ **Highlights / underlines / strikeout / sticky notes** parsed with colors and the underlying text.
- ✅ **SHA-256 integrity hash** of the extracted Markdown.
- ✅ **Raw Mode** (default) and **Engaging Mode** toggle with smooth transition.
- ✅ **Side-by-side** original PDF + extracted content view.
- ✅ **Search / Copy-all / Download (MD/HTML/TXT) / Zoom / Dark mode.**
- ✅ **Any language** — UTF-8 throughout.

## Engaging Mode — the unbreakable contract

The LLM receives the following system prompt and a `temperature: 0` config:

```
You are a deterministic formatting engine for PDF-extracted content.
HARD RULES — these are inviolable:
1. Never change, add, remove, paraphrase, translate, summarize, or reorder any words, numbers, names, dates, punctuation, or data points.
2. Preserve every character of the input exactly, including whitespace-significant content inside tables.
3. You may ONLY add presentation: Markdown headings, **bold**, *italics*, <u>underline</u>, bullet/numbered lists, blockquotes, relatable emojis next to headings (📊 💡 ⚠️ 📝 📈 📉 🟡 🟢 🔴), and callout blockquotes.
...
```

The UI explicitly shows which provider/model rendered the Engaging view so a reviewer can always diff the two modes.

## Repository layout

```
backend/               FastAPI service
  app/
    extractor.py       multi-engine extraction + OCR + anti-hallucination flags
    engaging.py        LLM-driven Engaging Mode (OpenAI / Anthropic / local fallback)
    main.py            /extract, /engaging, /download, /healthz
    models.py          pydantic schemas
  tests/make_sample_pdf.py    generates a sample PDF with text, table, image, highlight
frontend/              Next.js 14 app (App Router)
  src/app/page.tsx     upload, preview, Raw/Engaging toggle, search, copy, download
```

## Running locally

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
# Optional: Engaging Mode API key (otherwise it uses a deterministic local reformatter)
export LLM_PROVIDER=clod     # "clod" | "openai" | "anthropic"
export CLOD_API_KEY=clod-... # grab one free at https://app.clod.io
# export CLOD_MODEL="DeepSeek V3"
# or: export OPENAI_API_KEY=sk-...
# or: export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Requires system packages: `tesseract-ocr` and `poppler-utils`.

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
    "overall_confidence": 0.97
  },
  "raw_markdown": "# sample.pdf\n\n---\n\n## Page 1\n..."
}
```

### `POST /engaging`
JSON `{ "raw_markdown": "..." }`. Returns `{ engaging_markdown, provider, model, sha256_input }`.

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
| Strict zero-temperature system prompt for Engaging Mode | `backend/app/engaging.py` |
| `sha256_input` returned by `/engaging` so caller can verify input integrity | `/engaging` response |

## Roadmap (post-MVP)

- [ ] Camelot / Tabula for complex merged-cell tables
- [ ] Celery/Bull queue + Server-Sent Events for 500+ page PDFs
- [ ] Azure Document Intelligence as a premium OCR path
- [ ] Batch uploads + shareable expiring links
- [ ] Voice read-aloud with Engaging-Mode cues
- [ ] True annotation-JSON export

## License

MIT
