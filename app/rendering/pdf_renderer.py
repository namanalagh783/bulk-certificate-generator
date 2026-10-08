"""PdfCertificateRenderer: draws a `CertificateContent` onto a `CertificateTemplate` with ReportLab.

The renderer is a thin "read the spec, draw it" loop — all the layout decisions live in
`template.py` as data (ADR-006). The real logic here is (1) auto-shrinking/wrapping text that
can vary a lot in length (recipient name, program name, issuer) and (2) a handful of small
vector-drawing helpers for the decorative frame/seal added in ADR-021.
"""

import math
from datetime import date
from io import BytesIO
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.rendering.base import CertificateContent, RenderError
from app.rendering.fonts import DEFAULT_FONT_DIR, FontSet, load_fonts
from app.rendering.template import CLASSIC_V1, CertificateTemplate, FittedText, FixedText


def _format_issue_date(d: date) -> str:
    """Format like "1 October 2026". `%-d`/`%e` aren't portable across platforms (Windows), so
    the day is built separately and zero-padding is avoided by using `int` -> `str`.
    """
    return f"{d.day} {d:%B %Y}"


def _break_word_by_chars(word: str, font_name: str, size: float, max_width: float) -> list[str]:
    """Split a single word into chunks that each fit `max_width`, used when wrapping can't rely
    on spaces (e.g. one very long token)."""
    chunks: list[str] = []
    current = ""
    for ch in word:
        candidate = current + ch
        # `or not current` guarantees at least one character is placed per chunk, so this
        # always terminates even if a single character is wider than max_width.
        if pdfmetrics.stringWidth(candidate, font_name, size) <= max_width or not current:
            current = candidate
        else:
            chunks.append(current)
            current = ch
    if current:
        chunks.append(current)
    return chunks


def _wrap_greedy(text: str, font_name: str, size: float, max_width: float) -> list[str]:
    """Greedy word-wrap `text` at a fixed font size, using as many lines as it takes.

    Unlike a capped wrap, this never stops early: every word (or, for a word wider than
    `max_width` alone, every character chunk from `_break_word_by_chars`) ends up on some line.
    That is the property `fit_text` depends on to guarantee it never drops text.
    """
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font_name, size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if pdfmetrics.stringWidth(word, font_name, size) <= max_width:
            current = word
        else:
            chunks = _break_word_by_chars(word, font_name, size, max_width)
            lines.extend(chunks[:-1])
            current = chunks[-1] if chunks else ""
    if current:
        lines.append(current)
    return lines or [""]


#: The smallest size `fit_text` will ever shrink to. Below this, built-in PDF fonts (and the
#: optional DejaVu TTFs) stop being comfortably legible on a printed certificate, so instead of
#: shrinking further the renderer accepts a 3rd (or later) line at this size — still never
#: dropping text, just trading font size for line count once both are nearly exhausted.
_FIT_FLOOR_FONT_SIZE = 7.0

#: Step used while hunting for the largest size at which long text still wraps onto 2 lines.
#: Finer than the 1pt step used above `min_size` because this range is already small and every
#: extra half-point of legibility matters on a fixed-length field like a 200-character program.
_FIT_SHRINK_STEP = 0.5


def fit_text(
    text: str, font_name: str, max_size: float, min_size: float, max_width: float
) -> tuple[list[str], float]:
    """Shrink `text` from `max_size` down to `min_size` to fit `max_width` on one line.

    If it still doesn't fit at `min_size`, this keeps shrinking (in finer steps, down to
    `_FIT_FLOOR_FONT_SIZE`) to find the largest size at which the *whole* text wraps onto at
    most 2 lines — 2 lines reads better than 3+, but only when nothing is lost to get there.
    If even the floor size needs more than 2 lines, this returns however many lines the floor
    size needs: a long-but-complete certificate beats a short-but-truncated one every time.

    Returns the lines to draw and the font size they were drawn at.
    """
    size = max_size
    while size >= min_size:
        if pdfmetrics.stringWidth(text, font_name, size) <= max_width:
            return [text], size
        size -= 1

    size = min_size
    while size > _FIT_FLOOR_FONT_SIZE:
        lines = _wrap_greedy(text, font_name, size, max_width)
        if len(lines) <= 2:
            return lines, size
        size -= _FIT_SHRINK_STEP

    return _wrap_greedy(text, font_name, _FIT_FLOOR_FONT_SIZE, max_width), _FIT_FLOOR_FONT_SIZE


#: Fixed ascent/descent fractions of a font's size, used to approximate how far a line of text
#: reaches above/below its baseline. Querying exact per-glyph font metrics would be more
#: precise, but these elements only need "close enough to guarantee no visual collision" — a
#: simple, explainable constant beats a more accurate but harder-to-justify lookup table.
_ASCENT_FRACTION = 0.75
_DESCENT_FRACTION = 0.25


def _text_band(baseline_y: float, font_size: float) -> tuple[float, float]:
    """The approximate (top, bottom) extent of one line of text around its `baseline_y`."""
    return baseline_y + font_size * _ASCENT_FRACTION, baseline_y - font_size * _DESCENT_FRACTION


def _line_baselines(center_y: float, line_gap: float, count: int) -> list[float]:
    """Baselines for `count` lines stacked `line_gap` points apart, centered as a block on
    `center_y`. `count == 1` and `count == 2` reduce to the renderer's original hand-written
    single-line and two-line positions; this one formula covers those and any further line
    count `fit_text` might return, so drawing code never special-cases "how many lines".
    """
    return [center_y + ((count - 1) / 2 - index) * line_gap for index in range(count)]


def _fitted_lines_and_extremes(
    spec: FittedText, text: str, font_name: str, max_width: float
) -> tuple[list[str], float, float, float]:
    """Run `fit_text` for a `FittedText` field and return its lines, font size, and the
    baselines of its highest and lowest drawn lines.

    Shared by `_draw_fitted` (which draws every line) and `layout_bands`/`_compute_divider_y`
    (which only need the vertical extent) so both read off the exact same geometry — the
    fix-cycle 2 divider bug was two pieces of layout math that could silently drift apart.
    """
    lines, size = fit_text(text, font_name, spec.max_font_size, spec.min_font_size, max_width)
    baselines = _line_baselines(spec.y, spec.line_gap, len(lines))
    return lines, size, baselines[0], baselines[-1]


def _star_polygon_points(
    cx: float, cy: float, spikes: int, outer_radius: float, inner_radius: float
) -> list[tuple[float, float]]:
    """Vertices of a `spikes`-pointed star/rosette, alternating `outer_radius`/`inner_radius`.

    Walking `2 * spikes` vertices equally spaced by angle and alternating the radius is the
    standard way to build a star polygon; starting at `-pi/2` points the first spike straight up,
    which is how a seal/badge is conventionally oriented.
    """
    points = []
    step = math.pi / spikes
    for i in range(2 * spikes):
        radius = outer_radius if i % 2 == 0 else inner_radius
        angle = i * step - math.pi / 2
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


class PdfCertificateRenderer:
    """Renders `CertificateContent` into certificate PDF bytes using the given template/fonts."""

    def __init__(
        self,
        template: CertificateTemplate = CLASSIC_V1,
        font_dir: Path = DEFAULT_FONT_DIR,
    ) -> None:
        self._template = template
        self._fonts: FontSet = load_fonts(font_dir)

    def render(self, content: CertificateContent) -> bytes:
        """Draw one certificate and return the PDF bytes. Any failure becomes a `RenderError`."""
        try:
            buffer = BytesIO()
            pdf = canvas.Canvas(buffer, pagesize=self._template.page_size, invariant=1)
            pdf.setTitle(f"Certificate {content.certificate_number}")
            self._draw(pdf, content)
            pdf.showPage()
            pdf.save()
            return buffer.getvalue()
        except Exception as exc:
            # Short, traceback-free message (CLAUDE.md); the original exception is chained for
            # anyone reading logs, but never surfaced to API clients.
            raise RenderError(f"Failed to render certificate {content.certificate_number}") from exc

    def _draw(self, pdf: canvas.Canvas, content: CertificateContent) -> None:
        template = self._template
        width, _ = template.page_size
        center_x = width / 2

        self._draw_background(pdf)
        self._draw_border(pdf)
        self._draw_corner_ornaments(pdf)

        self._draw_fitted(pdf, template.title, content.title, center_x)
        self._draw_divider(pdf, center_x, self._compute_divider_y(content))
        self._draw_fixed(pdf, template.salutation, center_x)
        self._draw_fitted(pdf, template.recipient_name, content.recipient_name, center_x)
        self._draw_fixed(pdf, template.completion_line, center_x)
        self._draw_fitted(pdf, template.program_name, content.program_name, center_x)

        issued_by_text = template.issued_by_template.format(
            issuer=content.issuer_name, date=_format_issue_date(content.issue_date)
        )
        self._draw_fitted(pdf, template.issued_by, issued_by_text, center_x)

        self._draw_date_block(pdf, content, center_x)
        self._draw_seal(pdf, center_x)
        self._draw_signature_block(pdf, content, center_x)

        footer_text = template.footer_template.format(number=content.certificate_number)
        pdf.setFillColor(template.footer.color)
        pdf.setFont(getattr(self._fonts, template.footer.font_role), template.footer.font_size)
        pdf.drawCentredString(center_x, template.footer.y, footer_text)

    def _draw_background(self, pdf: canvas.Canvas) -> None:
        """Fill the whole page with the parchment color before anything else is drawn.

        Drawn first (and as a plain full-page `rect`) so every later shape/text simply paints
        on top of it — no need to track "what's behind me" anywhere else in the renderer.
        """
        width, height = self._template.page_size
        pdf.setFillColor(self._template.palette.parchment)
        pdf.rect(0, 0, width, height, stroke=0, fill=1)

    def _draw_border(self, pdf: canvas.Canvas) -> None:
        """Two concentric rectangles: a thick navy outer frame, a thin gold inner frame."""
        template = self._template
        width, height = template.page_size
        border = template.border

        pdf.setStrokeColor(template.palette.navy)
        pdf.setLineWidth(border.outer_width)
        inset = border.outer_inset
        pdf.rect(inset, inset, width - 2 * inset, height - 2 * inset, stroke=1, fill=0)

        pdf.setStrokeColor(template.palette.gold)
        pdf.setLineWidth(border.inner_width)
        inset = border.inner_inset
        pdf.rect(inset, inset, width - 2 * inset, height - 2 * inset, stroke=1, fill=0)

    def _draw_corner_ornaments(self, pdf: canvas.Canvas) -> None:
        """A small gold diamond at each of the 4 corners, sitting between the border rectangles
        so the frame reads as one deliberate ornamental unit rather than two bare rules.
        """
        template = self._template
        width, height = template.page_size
        ornament = template.corner_ornament
        pdf.setFillColor(template.palette.gold)
        for cx, cy in (
            (ornament.inset, ornament.inset),
            (width - ornament.inset, ornament.inset),
            (ornament.inset, height - ornament.inset),
            (width - ornament.inset, height - ornament.inset),
        ):
            self._draw_diamond(pdf, cx, cy, ornament.size)

    def _draw_diamond(self, pdf: canvas.Canvas, cx: float, cy: float, half_size: float) -> None:
        """A filled diamond (rotated square) centered at `(cx, cy)` — the one shape reused by
        both the corner ornaments and the title divider, so it lives in its own method.
        """
        path = pdf.beginPath()
        path.moveTo(cx, cy + half_size)
        path.lineTo(cx + half_size, cy)
        path.lineTo(cx, cy - half_size)
        path.lineTo(cx - half_size, cy)
        path.close()
        pdf.drawPath(path, stroke=0, fill=1)

    def _compute_divider_y(self, content: CertificateContent) -> float:
        """Place the divider midway between the title's lowest line and the salutation's cap
        height, so it always sits in whatever gap actually exists between them — including when
        the title wraps onto a 2nd line and that gap shrinks (fix-cycle 2, item 1).

        A fixed `y` (the previous design) can't do this: it was tuned for a 1-line title at one
        page position, and stopped matching reality the moment either moved.
        """
        template = self._template
        title_font = getattr(self._fonts, template.title.font_role)
        _, _, _, title_lowest_baseline = _fitted_lines_and_extremes(
            template.title, content.title, title_font, template.text_max_width
        )
        salutation = template.salutation
        salutation_cap_top = salutation.y + salutation.font_size * _ASCENT_FRACTION
        return (title_lowest_baseline + salutation_cap_top) / 2

    def _draw_divider(self, pdf: canvas.Canvas, center_x: float, y: float) -> None:
        """The short-line / diamond / short-line ornament drawn at `y` (see
        `_compute_divider_y`)."""
        divider = self._template.divider
        pdf.setStrokeColor(self._template.palette.gold)
        pdf.setLineWidth(1)
        pdf.line(center_x - divider.gap - divider.segment_length, y, center_x - divider.gap, y)
        pdf.line(center_x + divider.gap, y, center_x + divider.gap + divider.segment_length, y)
        pdf.setFillColor(self._template.palette.gold)
        self._draw_diamond(pdf, center_x, y, divider.diamond_half_size)

    def _draw_seal(self, pdf: canvas.Canvas, center_x: float) -> None:
        """Draw a vector seal/badge: a thick gold ring, a parchment face, a thin gold inner
        ring, and a centred star/rosette — no text (fix-cycle 1, item 2: a caption small enough
        to fit this badge without crossing the star was unreadable, so the badge stays purely
        graphical instead).

        No image asset is used (ADR-021). The outer ring is a solid gold disk with a smaller
        parchment disk painted on top, which leaves exactly the ring-shaped sliver between
        `outer_radius` and `face_radius` showing gold — cheaper than stroking an annulus shape
        directly. The thin inner ring is then a single stroked (unfilled) circle, and the
        rosette is a filled path through `_star_polygon_points`, sized to stay inside that inner
        ring so no two decorative elements overlap.
        """
        template = self._template
        seal = template.seal
        cy = seal.y

        pdf.setFillColor(template.palette.gold)
        pdf.circle(center_x, cy, seal.outer_radius, stroke=0, fill=1)
        pdf.setFillColor(template.palette.parchment)
        pdf.circle(center_x, cy, seal.face_radius, stroke=0, fill=1)
        pdf.setStrokeColor(template.palette.gold)
        pdf.setLineWidth(1)
        pdf.circle(center_x, cy, seal.inner_ring_radius, stroke=1, fill=0)

        points = _star_polygon_points(
            center_x, cy, seal.star_points, seal.star_outer_radius, seal.star_inner_radius
        )
        path = pdf.beginPath()
        path.moveTo(*points[0])
        for x, y in points[1:]:
            path.lineTo(x, y)
        path.close()
        pdf.setFillColor(template.palette.gold)
        pdf.drawPath(path, stroke=0, fill=1)

    def _draw_fixed(self, pdf: canvas.Canvas, spec: FixedText, center_x: float) -> None:
        pdf.setFillColor(spec.color)
        pdf.setFont(getattr(self._fonts, spec.font_role), spec.font_size)
        pdf.drawCentredString(center_x, spec.y, spec.text)

    def _draw_fitted(
        self, pdf: canvas.Canvas, spec: FittedText, text: str, center_x: float
    ) -> None:
        """Draw every line `fit_text` returns, stacked `line_gap` apart and centered on `spec.y`.

        `fit_text` can return 1, 2, or (rarely, for pathological input) more lines, and none of
        them may be skipped — that's the whole point of fix-cycle 1 item 1. `_line_baselines`
        is the one formula that produces the old hand-written 1-line and 2-line positions as its
        `count == 1` and `count == 2` special cases, so this loop replaces both without changing
        their output.
        """
        font = getattr(self._fonts, spec.font_role)
        lines, size = fit_text(
            text, font, spec.max_font_size, spec.min_font_size, self._template.text_max_width
        )
        baselines = _line_baselines(spec.y, spec.line_gap, len(lines))
        pdf.setFillColor(spec.color)
        pdf.setFont(font, size)
        for line, y in zip(lines, baselines, strict=True):
            pdf.drawCentredString(center_x, y, line)

    def layout_bands(self, content: CertificateContent) -> list[tuple[str, float, float]]:
        """The (name, top, bottom) vertical extent of every element `_draw` draws, in the same
        page-bottom-origin y-coordinates drawing uses.

        Built from the exact same `fit_text` calls and `_compute_divider_y` formula the drawing
        methods use, rather than a parallel set of formulas copied into a test file — that kind
        of copy is exactly how fix-cycle 2's divider/salutation overlap slipped past fix-cycle
        1's tests, since nothing forced the copies to stay in sync with the real layout.
        A layout test can use this to assert no two bands overlap and all sit inside the border.
        """
        template = self._template
        bands: list[tuple[str, float, float]] = []

        def fitted_band(name: str, spec: FittedText, text: str) -> None:
            font = getattr(self._fonts, spec.font_role)
            _, size, highest, lowest = _fitted_lines_and_extremes(
                spec, text, font, template.text_max_width
            )
            top, _ = _text_band(highest, size)
            _, bottom = _text_band(lowest, size)
            bands.append((name, top, bottom))

        def fixed_band(name: str, spec: FixedText) -> None:
            top, bottom = _text_band(spec.y, spec.font_size)
            bands.append((name, top, bottom))

        fitted_band("title", template.title, content.title)

        divider_y = self._compute_divider_y(content)
        half = template.divider.diamond_half_size
        bands.append(("divider", divider_y + half, divider_y - half))

        fixed_band("salutation", template.salutation)
        fitted_band("recipient_name", template.recipient_name, content.recipient_name)
        fixed_band("completion_line", template.completion_line)
        fitted_band("program_name", template.program_name, content.program_name)

        issued_by_text = template.issued_by_template.format(
            issuer=content.issuer_name, date=_format_issue_date(content.issue_date)
        )
        fitted_band("issued_by", template.issued_by, issued_by_text)

        # The date, seal and signature elements sit side-by-side (left/center/right of the same
        # row), not stacked, so they're one combined "bottom_block" band rather than 3 separate
        # ones — 3 separate bands would "overlap" by this function's y-only logic even though
        # they never visually collide, since this function (deliberately, per the brief) doesn't
        # track x-position.
        seal = template.seal
        seal_top, seal_bottom = seal.y + seal.outer_radius, seal.y - seal.outer_radius

        date_top = (
            template.date_caption_y
            + template.date_line_offset
            + template.date_value_gap
            + template.date_value_font_size * _ASCENT_FRACTION
        )
        date_bottom = template.date_caption_y - template.date_caption_font_size * _DESCENT_FRACTION

        sig_top = template.signature_y + template.signature_line_offset
        sig_bottom = template.signature_y - template.signature_font_size * _DESCENT_FRACTION

        bands.append(
            (
                "bottom_block",
                max(seal_top, date_top, sig_top),
                min(seal_bottom, date_bottom, sig_bottom),
            )
        )

        return bands

    def _draw_date_block(
        self, pdf: canvas.Canvas, content: CertificateContent, center_x: float
    ) -> None:
        """Left-of-center block: the formatted issue date above a short gold rule, a "Date"
        caption below it — mirrors `_draw_signature_block` on the right.
        """
        template = self._template
        x = center_x + template.date_x_offset
        line_y = template.date_caption_y + template.date_line_offset
        half_width = template.date_line_width / 2

        pdf.setStrokeColor(template.palette.gold)
        pdf.setLineWidth(1)
        pdf.line(x - half_width, line_y, x + half_width, line_y)

        pdf.setFillColor(template.palette.navy)
        pdf.setFont(self._fonts.body_bold, template.date_value_font_size)
        date_text = _format_issue_date(content.issue_date)
        pdf.drawCentredString(x, line_y + template.date_value_gap, date_text)

        pdf.setFillColor(template.palette.grey)
        pdf.setFont(self._fonts.body, template.date_caption_font_size)
        pdf.drawCentredString(x, template.date_caption_y, "Date")

    def _draw_signature_block(
        self, pdf: canvas.Canvas, content: CertificateContent, center_x: float
    ) -> None:
        """Right-of-center block: "<name>, <title>" below a short gold rule, or whichever of the
        two is present. Omitted entirely when both are absent (no rule to sign nothing under).
        """
        name, title = content.signatory_name, content.signatory_title
        if not name and not title:
            return
        template = self._template
        x = center_x + template.signature_x_offset
        half_width = template.signature_line_width / 2
        line_y = template.signature_y + template.signature_line_offset

        pdf.setStrokeColor(template.palette.gold)
        pdf.setLineWidth(1)
        pdf.line(x - half_width, line_y, x + half_width, line_y)

        text = f"{name}, {title}" if name and title else (name or title or "")
        pdf.setFillColor(template.palette.navy)
        pdf.setFont(self._fonts.body, template.signature_font_size)
        pdf.drawCentredString(x, template.signature_y, text)
