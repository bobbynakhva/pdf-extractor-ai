"""PDF extraction engine.

Fallback chain: PyMuPDF (fitz) -> pdfplumber -> pypdf.
OCR via pytesseract for pages with no extractable text.
Images, tables, annotations (highlights), metadata are all harvested.
Anti-hallucination: if a step fails we emit a `flag` block noting it,
we never fabricate content.
"""
from __future__ import annotations

import base64
import hashlib
import html as html_mod
import io
import logging
import re
from typing import Any

from .models import (
    Annotation,
    Bbox,
    ExtractedImage,
    ExtractedTable,
    ExtractionResult,
    Page,
    PageBlock,
)

log = logging.getLogger(__name__)

# Resolution (dots-per-inch) for the "PDF Layout" raster. 150 balances
# legibility (high-DPI fonts) against payload size. Override with env var.
import os as _os

_RENDER_DPI = int(_os.getenv("PDF_RENDER_DPI", "150"))

# PDF annotation subtype codes we care about (PyMuPDF).
HIGHLIGHT_SUBTYPES = {"Highlight", "Underline", "Squiggly", "StrikeOut"}
TEXT_ANNOT_SUBTYPES = {"Text", "FreeText", "Stamp", "Ink"}
LINK_SUBTYPES = {"Link"}


def _rgb_to_hex(color: tuple[float, float, float] | list[float] | None) -> str | None:
    if not color:
        return None
    try:
        r, g, b = color[:3]
        return "#{:02x}{:02x}{:02x}".format(
            int(round(r * 255)), int(round(g * 255)), int(round(b * 255))
        )
    except Exception:
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_with_pymupdf(pdf_bytes: bytes, result: ExtractionResult) -> None:
    import fitz  # type: ignore

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        result.metadata.update(doc.metadata or {})
        result.page_count = doc.page_count

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            rect = page.rect
            page_model = Page(
                number=page_index + 1,
                width=float(rect.width),
                height=float(rect.height),
            )

            # --- Text ---
            try:
                text = page.get_text("text") or ""
            except Exception as exc:
                text = ""
                page_model.blocks.append(
                    PageBlock(
                        kind="flag",
                        content=f"Text extraction failed on page {page_index + 1}: {exc}",
                        flagged=True,
                        confidence=0.0,
                    )
                )
                result.warnings.append(
                    f"Text extraction failed on page {page_index + 1}: {exc}"
                )

            page_model.plain_text = text
            if text.strip():
                page_model.blocks.append(
                    PageBlock(kind="text", content=text, confidence=0.98)
                )
            else:
                # Possibly a scanned page; try OCR.
                try:
                    ocr_text = _ocr_page(page)
                    if ocr_text.strip():
                        page_model.used_ocr = True
                        page_model.plain_text = ocr_text
                        page_model.blocks.append(
                            PageBlock(
                                kind="ocr_text",
                                content=ocr_text,
                                confidence=0.75,
                                note="OCR extracted (scanned page)",
                            )
                        )
                    else:
                        page_model.blocks.append(
                            PageBlock(
                                kind="flag",
                                content=f"No text found on page {page_index + 1} (OCR also empty).",
                                flagged=True,
                                confidence=0.0,
                            )
                        )
                except Exception as exc:
                    page_model.blocks.append(
                        PageBlock(
                            kind="flag",
                            content=f"OCR failed on page {page_index + 1}: {exc}",
                            flagged=True,
                            confidence=0.0,
                        )
                    )
                    result.warnings.append(f"OCR failed on page {page_index + 1}: {exc}")

            # --- Images ---
            try:
                for img_index, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    try:
                        base = doc.extract_image(xref)
                        img_bytes = base["image"]
                        ext = base.get("ext", "png")
                        data_url = (
                            f"data:image/{ext};base64,"
                            + base64.b64encode(img_bytes).decode("ascii")
                        )
                        bbox = None
                        try:
                            for rect_info in page.get_image_rects(xref):
                                bbox = Bbox(
                                    x0=float(rect_info.x0),
                                    y0=float(rect_info.y0),
                                    x1=float(rect_info.x1),
                                    y1=float(rect_info.y1),
                                )
                                break
                        except Exception:
                            pass
                        ex_img = ExtractedImage(
                            page=page_index + 1,
                            index=img_index,
                            ext=ext,
                            width=int(base.get("width", 0)),
                            height=int(base.get("height", 0)),
                            bbox=bbox,
                            data_url=data_url,
                        )
                        result.images.append(ex_img)
                        page_model.blocks.append(
                            PageBlock(kind="image", image=ex_img, confidence=1.0)
                        )
                    except Exception as exc:
                        page_model.blocks.append(
                            PageBlock(
                                kind="flag",
                                content=f"Failed to extract image {img_index} on page {page_index + 1}: {exc}",
                                flagged=True,
                                confidence=0.0,
                            )
                        )
            except Exception as exc:
                result.warnings.append(
                    f"Image list failed on page {page_index + 1}: {exc}"
                )

            # --- Annotations (highlights, notes, links) ---
            try:
                annot = page.first_annot
                while annot:
                    try:
                        info = annot.info or {}
                        subtype = annot.type[1] if annot.type else ""
                        kind = "annotation"
                        if subtype in HIGHLIGHT_SUBTYPES:
                            kind = subtype.lower()
                        elif subtype in TEXT_ANNOT_SUBTYPES:
                            kind = "note"
                        elif subtype in LINK_SUBTYPES:
                            kind = "link"

                        color_hex = _rgb_to_hex(annot.colors.get("stroke"))
                        # For highlights the color is usually "fill"
                        if not color_hex:
                            color_hex = _rgb_to_hex(annot.colors.get("fill"))

                        # Grab the highlighted text via quadpoints if available.
                        highlighted_text = None
                        try:
                            if subtype in HIGHLIGHT_SUBTYPES:
                                words = page.get_text("words")
                                rect = annot.rect
                                extracted = []
                                for w in words:
                                    wx0, wy0, wx1, wy1, wtxt, *_ = w
                                    if (
                                        wx0 >= rect.x0 - 1
                                        and wy0 >= rect.y0 - 1
                                        and wx1 <= rect.x1 + 1
                                        and wy1 <= rect.y1 + 1
                                    ):
                                        extracted.append(wtxt)
                                if extracted:
                                    highlighted_text = " ".join(extracted)
                        except Exception:
                            pass

                        r = annot.rect
                        a = Annotation(
                            kind=kind,
                            color=color_hex,
                            text=highlighted_text,
                            contents=info.get("content") or None,
                            bbox=Bbox(
                                x0=float(r.x0),
                                y0=float(r.y0),
                                x1=float(r.x1),
                                y1=float(r.y1),
                            ),
                            page=page_index + 1,
                        )
                        result.annotations.append(a)
                        page_model.blocks.append(
                            PageBlock(kind="annotation", annotation=a, confidence=0.95)
                        )
                    except Exception as exc:
                        result.warnings.append(
                            f"Annotation parse failed on page {page_index + 1}: {exc}"
                        )
                    annot = annot.next
            except Exception as exc:
                result.warnings.append(
                    f"Annotation iteration failed on page {page_index + 1}: {exc}"
                )

            # --- Pixel-perfect page render (for "PDF Layout" view) ---
            try:
                pix = page.get_pixmap(dpi=_RENDER_DPI, alpha=False)
                png_bytes = pix.tobytes("png")
                page_model.render_png = (
                    "data:image/png;base64,"
                    + base64.b64encode(png_bytes).decode("ascii")
                )
                page_model.render_width = int(pix.width)
                page_model.render_height = int(pix.height)
            except Exception as exc:
                result.warnings.append(
                    f"Page render failed on page {page_index + 1}: {exc}"
                )

            # --- Positioned HTML (kept as a lighter, text-selectable fallback) ---
            try:
                page_model.layout_html = _page_layout_html(page, page_index, result)
            except Exception as exc:
                result.warnings.append(
                    f"Layout HTML failed on page {page_index + 1}: {exc}"
                )

            result.pages.append(page_model)

        result.engines_used.append("pymupdf")
    finally:
        doc.close()


_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)
_PAGE_DIV_ID_RE = re.compile(r'<div\s+id="page\d+"\s*', re.IGNORECASE)


def _page_layout_html(page: Any, page_index: int, result: ExtractionResult) -> str:
    """Generate a positioned HTML snippet for one page.

    Uses PyMuPDF's ``get_text("html")`` which preserves fonts, sizes, colors,
    inline bold/italic, and absolute positioning. On top we overlay translucent
    rectangles for highlight annotations so they render inline with their
    original color.
    """
    html = page.get_text("html") or ""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html
    # PyMuPDF wraps each page in a `<div id="pageN" style="... width:... height:...">...</div>`.
    # We want to ensure the page div has position:relative so overlays anchor to it.
    body = _PAGE_DIV_ID_RE.sub(
        f'<div class="pdf-page" id="pdf-page-{page_index + 1}" ', body, count=1
    )
    # Inject position:relative into the page div's style so overlays anchor.
    body = re.sub(
        r'(class="pdf-page"[^>]*?style=")',
        r"\1position:relative;",
        body,
        count=1,
    )

    # Build highlight overlay divs from this page's annotations.
    overlays: list[str] = []
    page_num = page_index + 1
    for a in result.annotations:
        if a.page != page_num or not a.bbox:
            continue
        if a.kind not in {"highlight", "underline", "squiggly", "strikeout"}:
            continue
        color = a.color or "#ffff00"
        x = a.bbox.x0
        y = a.bbox.y0
        w = max(0.0, a.bbox.x1 - a.bbox.x0)
        h = max(0.0, a.bbox.y1 - a.bbox.y0)
        tooltip = html_mod.escape(a.text or a.contents or a.kind)
        if a.kind == "highlight":
            style = (
                f"position:absolute;left:{x:.2f}pt;top:{y:.2f}pt;"
                f"width:{w:.2f}pt;height:{h:.2f}pt;"
                f"background:{color};opacity:0.35;mix-blend-mode:multiply;"
                f"pointer-events:none;border-radius:1px;"
            )
        elif a.kind == "underline":
            style = (
                f"position:absolute;left:{x:.2f}pt;top:{(y + h - 1):.2f}pt;"
                f"width:{w:.2f}pt;height:1.2pt;background:{color};"
                f"pointer-events:none;"
            )
        elif a.kind == "strikeout":
            style = (
                f"position:absolute;left:{x:.2f}pt;top:{(y + h / 2):.2f}pt;"
                f"width:{w:.2f}pt;height:1.2pt;background:{color};"
                f"pointer-events:none;"
            )
        else:  # squiggly
            style = (
                f"position:absolute;left:{x:.2f}pt;top:{(y + h - 2):.2f}pt;"
                f"width:{w:.2f}pt;height:2pt;border-bottom:1.2pt wavy {color};"
                f"pointer-events:none;"
            )
        overlays.append(
            f'<div class="pdf-annot pdf-annot-{a.kind}" '
            f'style="{style}" title="{tooltip}"></div>'
        )

    if overlays:
        # Insert overlays just before the closing </div> of the page div.
        body = body.rstrip()
        if body.endswith("</div>"):
            body = body[:-6] + "\n" + "\n".join(overlays) + "\n</div>"
        else:
            body = body + "\n" + "\n".join(overlays)
    return body


def _ocr_page(page: Any) -> str:
    """Render a fitz page to an image and OCR it."""
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(img)


def _extract_tables_pdfplumber(pdf_bytes: bytes, result: ExtractionResult) -> None:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    tables = page.extract_tables() or []
                except Exception as exc:
                    result.warnings.append(
                        f"pdfplumber table extraction failed on page {i + 1}: {exc}"
                    )
                    continue

                for t in tables:
                    # Drop fully-empty tables
                    rows = [
                        [("" if cell is None else str(cell)) for cell in row]
                        for row in t
                    ]
                    if not rows or all(all(not c.strip() for c in r) for r in rows):
                        continue
                    # crude confidence: fraction of non-empty cells
                    total = sum(len(r) for r in rows) or 1
                    filled = sum(1 for r in rows for c in r if c.strip())
                    conf = filled / total
                    flagged = conf < 0.4
                    ex = ExtractedTable(
                        page=i + 1,
                        rows=rows,
                        confidence=conf,
                        flagged=flagged,
                        note=(
                            "Sparse cells — review for accuracy." if flagged else None
                        ),
                    )
                    result.tables.append(ex)
                    # Attach to the page
                    if i < len(result.pages):
                        result.pages[i].blocks.append(
                            PageBlock(
                                kind="table",
                                table=ex,
                                confidence=conf,
                                flagged=flagged,
                            )
                        )
        result.engines_used.append("pdfplumber")
    except Exception as exc:
        result.warnings.append(f"pdfplumber failed: {exc}")


def _extract_with_pypdf_fallback(pdf_bytes: bytes, result: ExtractionResult) -> None:
    """Used only if PyMuPDF failed entirely."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    result.page_count = len(reader.pages)
    try:
        result.metadata.update({k: str(v) for k, v in (reader.metadata or {}).items()})
    except Exception:
        pass
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            result.warnings.append(f"pypdf text extract failed page {i + 1}: {exc}")
        p = Page(number=i + 1, width=0.0, height=0.0, plain_text=text)
        if text.strip():
            p.blocks.append(PageBlock(kind="text", content=text, confidence=0.7))
        else:
            p.blocks.append(
                PageBlock(
                    kind="flag",
                    content=f"pypdf found no text on page {i + 1}",
                    flagged=True,
                    confidence=0.0,
                )
            )
        result.pages.append(p)
    result.engines_used.append("pypdf")


def extract_pdf(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    result = ExtractionResult(
        filename=filename,
        page_count=0,
        pages=[],
    )
    # Try PyMuPDF first.
    try:
        _extract_with_pymupdf(pdf_bytes, result)
    except Exception as exc:
        log.exception("PyMuPDF extraction failed")
        result.warnings.append(f"PyMuPDF extraction failed: {exc}")
        try:
            _extract_with_pypdf_fallback(pdf_bytes, result)
        except Exception as exc2:
            result.warnings.append(f"pypdf fallback also failed: {exc2}")

    # Tables (additive, always try pdfplumber).
    _extract_tables_pdfplumber(pdf_bytes, result)

    # Concatenate plain text.
    result.plain_text = "\n\n".join(p.plain_text for p in result.pages)
    result.sha256 = _sha256_bytes(result.plain_text.encode("utf-8"))

    # Assemble the document-level positioned HTML for the "PDF Layout" view.
    layout_parts = [p.layout_html for p in result.pages if p.layout_html]
    if layout_parts:
        result.layout_html = '<div class="pdf-doc">\n' + "\n".join(layout_parts) + "\n</div>"

    # Overall confidence: average of all block confidences weighted by text length.
    total_w = 0.0
    total_c = 0.0
    for p in result.pages:
        for b in p.blocks:
            w = max(1, len(b.content))
            total_w += w
            total_c += b.confidence * w
    result.overall_confidence = (total_c / total_w) if total_w else 0.0

    return result


def result_to_raw_markdown(result: ExtractionResult) -> str:
    """Deterministic, lossless-ish Markdown rendering of the extracted content.

    This is the "Raw Mode" canonical text that is also fed into Engaging Mode
    so the LLM has the exact bytes to reformat.
    """
    lines: list[str] = []
    lines.append(f"# {result.filename}")
    lines.append("")
    if result.metadata:
        lines.append("<!-- metadata -->")
        for k, v in result.metadata.items():
            if v:
                lines.append(f"- **{k}**: {v}")
        lines.append("")

    for page in result.pages:
        lines.append(f"\n---\n\n## Page {page.number}\n")
        if page.used_ocr:
            lines.append("_(text recovered via OCR)_\n")
        for block in page.blocks:
            if block.kind in ("text", "ocr_text"):
                lines.append(block.content.rstrip())
                lines.append("")
            elif block.kind == "table" and block.table:
                lines.append(_render_md_table(block.table))
                if block.flagged:
                    lines.append(
                        f"> ⚠️ Needs verification — {block.note or 'low confidence table'}"
                    )
                lines.append("")
            elif block.kind == "image" and block.image:
                lines.append(
                    f"![page {block.image.page} image {block.image.index}]({block.image.data_url})"
                )
                lines.append("")
            elif block.kind == "annotation" and block.annotation:
                a = block.annotation
                snippet = a.text or a.contents or ""
                tag = a.kind.upper()
                color = a.color or ""
                if snippet:
                    lines.append(f"> [{tag} {color}] {snippet}")
                elif a.contents:
                    lines.append(f"> [{tag}] {a.contents}")
                lines.append("")
            elif block.kind == "flag":
                lines.append(f"> ⚠️ **EXTRACTION FLAG:** {block.content}")
                lines.append("")

    if result.warnings:
        lines.append("\n---\n\n### Extraction warnings\n")
        for w in result.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines).rstrip() + "\n"


def _render_md_table(t: ExtractedTable) -> str:
    if not t.rows:
        return ""
    header = t.rows[0]
    body = t.rows[1:] if len(t.rows) > 1 else []
    # escape pipes
    def esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", "<br>")

    md: list[str] = []
    md.append("| " + " | ".join(esc(c) for c in header) + " |")
    md.append("| " + " | ".join(["---"] * len(header)) + " |")
    for r in body:
        # pad/truncate row to header width
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        elif len(r) > len(header):
            r = r[: len(header)]
        md.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(md)
