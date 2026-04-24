from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Bbox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class Annotation(BaseModel):
    kind: str  # highlight, underline, squiggly, strikeout, text, link, ...
    color: Optional[str] = None  # hex like #ffff00
    text: Optional[str] = None
    contents: Optional[str] = None
    bbox: Optional[Bbox] = None
    page: int


class ExtractedImage(BaseModel):
    page: int
    index: int
    ext: str
    width: int
    height: int
    bbox: Optional[Bbox] = None
    data_url: str  # data:image/png;base64,...


class TableCell(BaseModel):
    text: str
    row: int
    col: int
    confidence: float = 1.0


class ExtractedTable(BaseModel):
    page: int
    rows: list[list[str]]
    confidence: float = 1.0
    flagged: bool = False
    note: Optional[str] = None


class PageBlock(BaseModel):
    kind: Literal["text", "table", "image", "annotation", "ocr_text", "flag"]
    content: str = ""
    html: Optional[str] = None
    table: Optional[ExtractedTable] = None
    image: Optional[ExtractedImage] = None
    annotation: Optional[Annotation] = None
    confidence: float = 1.0
    flagged: bool = False
    note: Optional[str] = None


class Page(BaseModel):
    number: int
    width: float
    height: float
    blocks: list[PageBlock] = Field(default_factory=list)
    plain_text: str = ""
    used_ocr: bool = False
    # Positioned HTML for this page (PyMuPDF's "html" output wrapped in a
    # `.pdf-page` div with highlight overlays). May be empty when the page
    # was extracted via a fallback engine.
    layout_html: str = ""
    # Pixel-perfect PNG render of the page (data URL). Produced by PyMuPDF
    # `get_pixmap` at a high DPI so the Layout view shows exactly what the
    # PDF renders to in any viewer — fonts, kerning, and all.
    render_png: str = ""
    # Natural PNG dimensions at render DPI. The frontend uses these to place
    # highlight overlays in the same coordinate space as the raster.
    render_width: int = 0
    render_height: int = 0


class ExtractionResult(BaseModel):
    filename: str
    page_count: int
    metadata: dict = Field(default_factory=dict)
    pages: list[Page]
    images: list[ExtractedImage] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    engines_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    plain_text: str = ""
    sha256: str = ""
    overall_confidence: float = 1.0
    # Concatenated positioned HTML for the whole document (all `.pdf-page`s
    # stacked vertically). Rendered by the frontend as the "PDF Layout" view.
    layout_html: str = ""


class EngagingRequest(BaseModel):
    raw_markdown: str
    model: Optional[str] = None


class EngagingResponse(BaseModel):
    engaging_markdown: str
    provider: str
    model: str
    sha256_input: str
