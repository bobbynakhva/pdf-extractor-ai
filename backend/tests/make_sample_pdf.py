"""Generate a small sample PDF with text, table, image and a highlight annotation."""
from __future__ import annotations

import io
from pathlib import Path

import fitz  # type: ignore
from PIL import Image, ImageDraw


def make_sample(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    rect = fitz.Rect(50, 50, 545, 120)
    page.insert_textbox(
        rect,
        "Section 3.1: Revenue Growth\n"
        "Revenue increased by 23.4% in Q2.\n"
        '"Market conditions remain favorable." — CEO',
        fontsize=12,
    )

    # Draw a tiny table-like block using text
    table_rect = fitz.Rect(50, 140, 300, 220)
    page.insert_textbox(
        table_rect,
        "Year  Rev   Growth\n"
        "2023  1.2M  15%\n"
        "2024  1.5M  23.4%",
        fontsize=11,
    )

    # Insert an image
    img_buf = io.BytesIO()
    pil = Image.new("RGB", (200, 120), "white")
    d = ImageDraw.Draw(pil)
    d.rectangle([10, 30, 60, 110], fill="#4caf50")
    d.rectangle([80, 10, 130, 110], fill="#2196f3")
    d.rectangle([150, 50, 200, 110], fill="#ff9800")
    pil.save(img_buf, format="PNG")
    img_rect = fitz.Rect(320, 140, 520, 260)
    page.insert_image(img_rect, stream=img_buf.getvalue())

    # Highlight annotation on "23.4%"
    # find location of that text
    for inst in page.search_for("23.4%"):
        annot = page.add_highlight_annot(inst)
        annot.set_colors(stroke=(1, 1, 0))
        annot.update()

    # Second page — a proper table via drawing lines + cells
    page2 = doc.new_page(width=595, height=842)
    rows = [
        ["Product", "Q1", "Q2", "Q3"],
        ["Widget A", "100", "120", "140"],
        ["Widget B", "80", "95", "110"],
        ["Widget C", "60", "72", "88"],
    ]
    x0, y0 = 60, 80
    cw, rh = 110, 28
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            r = fitz.Rect(x0 + ci * cw, y0 + ri * rh, x0 + (ci + 1) * cw, y0 + (ri + 1) * rh)
            page2.draw_rect(r, color=(0, 0, 0), width=0.6)
            page2.insert_textbox(r, cell, fontsize=11, align=fitz.TEXT_ALIGN_CENTER)

    doc.save(str(path))
    doc.close()


if __name__ == "__main__":
    out = Path(__file__).parent / "sample.pdf"
    make_sample(out)
    print(f"wrote {out}")
