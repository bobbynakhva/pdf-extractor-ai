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


class EngagingRequest(BaseModel):
    raw_markdown: str
    model: Optional[str] = None


class EngagingResponse(BaseModel):
    engaging_markdown: str
    provider: str
    model: str
    sha256_input: str
