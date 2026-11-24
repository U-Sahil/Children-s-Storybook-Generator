"""PDF assembly with ReportLab.

Layout (A4 portrait):
  * Cover page   - title, "A story for <name>", cover illustration, introduction
  * Story pages  - illustration on top (aspect ratio preserved), narration below,
                   page number in the footer
  * Moral page   - "The Lesson" with the story's moral

Safety features:
  * Missing / unreadable images are replaced by a clearly labelled box
    ("Illustration not generated") - never by fake art.
  * Long text flows onto continuation pages instead of overflowing.
  * Text is sanitised to characters the built-in PDF fonts can render.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from PIL import Image, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .errors import PDFError as PDFGenerationError
from .models import Page as StoryPage, Story

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_Y = 10 * mm
MAX_CONTINUATION_PAGES = 30  # hard stop so pathological text can never loop forever

INK = colors.HexColor("#2b2d42")
ACCENT = colors.HexColor("#ef6f6c")
SOFT = colors.HexColor("#fff4e6")
MUTED = colors.HexColor("#8d99ae")

_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
}


def sanitize_text(text: str) -> str:
    """Replace characters the built-in Helvetica/Times fonts cannot draw.

    (Emoji and non-Latin scripts would otherwise appear as black boxes.)
    """
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("cp1252", errors="ignore").decode("cp1252")


def _styles(body_size: float) -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=34, leading=40,
                                alignment=TA_CENTER, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica-Oblique", fontSize=16, leading=22,
                                   alignment=TA_CENTER, textColor=ACCENT),
        "intro": ParagraphStyle("intro", fontName="Times-Italic", fontSize=14, leading=20,
                                alignment=TA_CENTER, textColor=INK),
        "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=body_size,
                               leading=body_size * 1.45, alignment=TA_LEFT, textColor=INK),
        "heading": ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=26, leading=32,
                                  alignment=TA_CENTER, textColor=ACCENT),
        "moral": ParagraphStyle("moral", fontName="Times-Italic", fontSize=20, leading=29,
                                alignment=TA_CENTER, textColor=INK),
    }


class _PDFBuilder:
    def __init__(self, story: Story, story_dir: Path, output_path: Path):
        self.story = story
        self.story_dir = story_dir
        self.output_path = output_path
        self.c = canvas.Canvas(str(output_path), pagesize=A4)
        self.c.setTitle(sanitize_text(story.title))
        self.c.setAuthor("AI-Generated Children's Storybook")
        self.c.setSubject(f"A personalised story for {sanitize_text(story.input.child_name)}")
        self.page_no = 0
        self.warnings: list[str] = []

    # ------------------------------------------------------------------
    def _background(self) -> None:
        self.c.setFillColor(SOFT)
        self.c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    def _footer(self, number: Optional[int]) -> None:
        if number is None:
            return
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica", 10)
        self.c.drawCentredString(PAGE_W / 2, FOOTER_Y, f"- {number} -")

    def _new_page(self) -> None:
        if self.page_no:
            self.c.showPage()
        self.page_no += 1
        self._background()

    # ------------------------------------------------------------------
    def _draw_image(self, page: Optional[StoryPage], x: float, y: float, w: float, h: float) -> None:
        """Draw the page's illustration fitted inside the box, or a labelled placeholder."""
        if page is not None and page.image_path and not page.image_is_ai:
            note = f"Page {page.page_number} uses a simple offline sketch, not an AI image."
            if note not in self.warnings:
                self.warnings.append(note)
        path = self.story_dir / page.image_path if page and page.image_path else None
        if path is not None and path.is_file():
            try:
                with Image.open(path) as img:
                    iw, ih = img.size
                    img.verify()
                scale = min(w / iw, h / ih)
                dw, dh = iw * scale, ih * scale
                dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
                self.c.setFillColor(colors.white)
                self.c.roundRect(dx - 3, dy - 3, dw + 6, dh + 6, 6, stroke=0, fill=1)
                self.c.drawImage(str(path), dx, dy, dw, dh, mask="auto")
                return
            except (UnidentifiedImageError, OSError, ValueError):
                self.warnings.append(f"Image for page {page.page_number} is unreadable.")
        elif page is not None:
            self.warnings.append(f"Page {page.page_number} has no illustration.")

        # Clearly labelled placeholder (never presented as AI output).
        self.c.setStrokeColor(MUTED)
        self.c.setDash(6, 4)
        self.c.setFillColor(colors.white)
        self.c.roundRect(x, y, w, h, 8, stroke=1, fill=1)
        self.c.setDash()
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica-Oblique", 14)
        self.c.drawCentredString(x + w / 2, y + h / 2, "Illustration not generated")

    def _draw_fitted(self, text: str, style: ParagraphStyle, x: float, y: float, w: float, h: float,
                     valign: str = "top") -> None:
        """Draw short text (title, intro, moral) inside a box, shrinking the font to fit.

        If it still does not fit at 50% size, the text is cut at the box edge
        rather than overflowing the page.
        """
        safe = escape(sanitize_text(text))
        scale = 1.0
        while True:
            fitted = ParagraphStyle(f"{style.name}_fit", parent=style, fontSize=style.fontSize * scale,
                                    leading=style.leading * scale)
            para = Paragraph(safe, fitted)
            _, needed = para.wrap(w, 10_000)
            if needed <= h or scale <= 0.5:
                break
            scale -= 0.1
        if needed > h:
            parts = para.split(w, h)
            if parts:
                para = parts[0]
                _, needed = para.wrap(w, h)
        used = min(needed, h)
        top_offset = (h - used) / 2 if valign == "middle" else 0
        para.drawOn(self.c, x, y + h - used - top_offset)

    def _draw_flowing(self, para: Paragraph, x: float, y: float, w: float, h: float) -> Optional[Paragraph]:
        """Draw as much of ``para`` as fits in the box (top-aligned); return the rest or None.

        Always makes progress: if nothing can be placed (box smaller than one
        line) a PDFGenerationError is raised instead of looping forever.
        """
        _, needed = para.wrap(w, 10_000)
        if needed <= h:
            para.drawOn(self.c, x, y + h - needed)
            return None
        parts = para.split(w, h)
        if len(parts) < 2:
            raise PDFGenerationError("Text could not be laid out on the page (box too small).")
        head, tail = parts[0], parts[1]
        _, head_h = head.wrap(w, h)
        head.drawOn(self.c, x, y + h - head_h)
        return tail

    # ------------------------------------------------------------------
    def cover(self) -> None:
        story = self.story
        st = _styles(16)
        self._new_page()
        self.c.setFillColor(ACCENT)
        self.c.rect(0, PAGE_H - 12 * mm, PAGE_W, 12 * mm, stroke=0, fill=1)

        title_h = 45 * mm
        self._draw_fitted(story.title, st["title"], MARGIN, PAGE_H - 25 * mm - title_h, CONTENT_W, title_h,
                          valign="middle")
        cover_page = next((p for p in story.pages if p.image_path), story.pages[0] if story.pages else None)
        self._draw_image(cover_page, MARGIN, 98 * mm, CONTENT_W, 122 * mm)
        self._draw_fitted(f"A story for {story.input.child_name}", st["subtitle"], MARGIN, 84 * mm,
                          CONTENT_W, 12 * mm)
        self._draw_fitted(story.introduction, st["intro"], MARGIN + 8 * mm, 25 * mm,
                          CONTENT_W - 16 * mm, 52 * mm)

    def story_page(self, page: StoryPage) -> None:
        """One story page; long text continues on extra pages if needed."""
        img_h = 125 * mm
        img_y = PAGE_H - MARGIN - img_h
        text_h = img_y - 12 * mm - 18 * mm
        text_y = 18 * mm

        # Try shrinking the font first; only then fall back to continuation pages.
        text = sanitize_text(page.text)
        chosen = 20.0
        for size in (20.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0):
            chosen = size
            flow = Paragraph(escape(text), _styles(size)["body"])
            _, needed = flow.wrap(CONTENT_W, 10_000)
            if needed <= text_h:
                break

        self._new_page()
        self._draw_image(page, MARGIN, img_y, CONTENT_W, img_h)
        remaining = self._draw_flowing(Paragraph(escape(text), _styles(chosen)["body"]),
                                       MARGIN, text_y, CONTENT_W, text_h)
        self._footer(page.page_number)

        # Continuation pages (only for extremely long text); bounded so it can never loop forever.
        extra = 0
        while remaining is not None:
            extra += 1
            if extra > MAX_CONTINUATION_PAGES:
                raise PDFGenerationError(
                    f"Page {page.page_number} has too much text to fit in the PDF layout."
                )
            self._new_page()
            remaining = self._draw_flowing(remaining, MARGIN, MARGIN + 10 * mm, CONTENT_W,
                                           PAGE_H - 2 * MARGIN - 10 * mm)
            self._footer(page.page_number)

    def moral_page(self) -> None:
        st = _styles(16)
        self._new_page()
        self.c.setFillColor(colors.white)
        self.c.roundRect(MARGIN, 80 * mm, CONTENT_W, PAGE_H - 160 * mm, 14, stroke=0, fill=1)
        self._draw_fitted("The Lesson", st["heading"], MARGIN + 10 * mm, PAGE_H - 100 * mm,
                          CONTENT_W - 20 * mm, 20 * mm)
        self._draw_fitted(self.story.moral, st["moral"], MARGIN + 10 * mm, 100 * mm,
                          CONTENT_W - 20 * mm, PAGE_H - 215 * mm, valign="middle")
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica-Oblique", 11)
        self.c.drawCentredString(PAGE_W / 2, 60 * mm, f"The End - for {sanitize_text(self.story.input.child_name)}")
        self.c.setFont("Helvetica", 9)
        sketches = any(p.image_path and not p.image_is_ai for p in self.story.pages)
        self.c.drawCentredString(
            PAGE_W / 2, 20 * mm,
            "Story written with AI. Some pictures are simple offline sketches." if sketches
            else "Story and illustrations were generated with AI.",
        )

    def build(self) -> list[str]:
        self.cover()
        for page in self.story.pages:
            self.story_page(page)
        self.moral_page()
        self.c.showPage()
        self.c.save()
        return self.warnings


def build_pdf(story: Story, story_dir: Path, output_path: Path) -> tuple[Path, list[str]]:
    """Create the PDF. Returns (path, warnings).

    ``story_dir`` is the folder that image paths in the story are relative to.
    Raises ``PDFGenerationError`` if the file cannot be produced.
    """
    if not story.pages:
        raise PDFGenerationError("The story has no pages to export.")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        warnings = _PDFBuilder(story, story_dir, output_path).build()
    except PDFGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFGenerationError(f"Could not create the PDF: {exc}") from exc
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise PDFGenerationError("The PDF file was not written correctly.")
    return output_path, warnings
