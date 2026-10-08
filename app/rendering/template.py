"""CLASSIC_V1 certificate layout: a single, immutable layout spec drawn by `PdfCertificateRenderer`.

Everything a human would tweak about the look of the certificate (margins, font sizes, wording,
colors, vertical positions) lives here as plain data, not scattered through drawing code. This
keeps the renderer itself a thin "read the spec, draw it" loop (ARCHITECTURE §10, ADR-006).

ADR-021 added a navy-and-gold decorative design on top of the same data shape: a palette, a
two-color border with corner ornaments, a title divider, and a seal/date/signature bottom layout.
"""

from dataclasses import dataclass

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, landscape

#: A4 landscape, in points: (width, height) ~= (841.9, 595.3).
PAGE_SIZE = landscape(A4)


@dataclass(frozen=True)
class Palette:
    """The certificate's four colors (ADR-021). Kept as named roles, not raw hex, so drawing
    code says *why* a color is used ("navy" for formal ink) rather than repeating hex strings.
    """

    navy: Color
    gold: Color
    parchment: Color
    grey: Color


#: Deep navy + antique gold on a parchment background — the "formal certificate" palette
#: requested in ADR-021. Grey (not black) for body copy keeps it softer than the navy headlines.
CLASSIC_PALETTE = Palette(
    navy=HexColor("#1F2A44"),
    gold=HexColor("#B8913A"),
    parchment=HexColor("#FBF7EC"),
    grey=HexColor("#4A4A4A"),
)


@dataclass(frozen=True)
class FixedText:
    """A single line of fixed, known-short text, centered at `y` (points from the page bottom).

    `font_role` names a `FontSet` attribute (e.g. "body") rather than storing a font name
    directly, so swapping the font family (DejaVu vs. built-in) never touches this spec.
    """

    text: str
    font_size: float
    y: float
    color: Color
    font_role: str = "body"


@dataclass(frozen=True)
class FittedText:
    """A line whose text is only known at render time and may be long (name/program/issuer).

    The renderer shrinks the font from `max_font_size` down to `min_font_size` to make it fit
    `text_max_width` on one line. If it still doesn't fit, it keeps shrinking in finer steps down
    to a hard floor while trying to wrap onto 2 lines; only if that floor still isn't enough does
    it use a 3rd (or later) line — text is never dropped (see `pdf_renderer.fit_text`). Lines are
    stacked `line_gap` points apart, vertically centered as a block on `y`.
    """

    max_font_size: float
    min_font_size: float
    y: float
    line_gap: float
    color: Color
    font_role: str = "body"


@dataclass(frozen=True)
class BorderSpec:
    """Two concentric rectangles inset from the page edge, each with its own color/weight:
    a thick navy outer frame and a thin gold inner frame — the "ornamental border" of ADR-021.
    """

    outer_inset: float
    outer_width: float
    inner_inset: float
    inner_width: float


@dataclass(frozen=True)
class CornerOrnamentSpec:
    """A small gold diamond centered at `inset` from each page corner, drawn between the two
    border rectangles so it reads as part of the frame rather than floating text decoration.
    """

    inset: float
    size: float


@dataclass(frozen=True)
class DividerSpec:
    """The short-line / diamond / short-line ornament drawn between the title and the
    salutation.

    `gap` is the empty space between each line segment and the center diamond; `segment_length`
    is each line's length, measured outward from that gap. There is deliberately no `y` field:
    fix-cycle 2 found the divider overlapping the salutation because a *fixed* `y` didn't move
    when the title's position (or line count) changed, so the renderer now computes `y` at
    render time from the title and salutation it is actually sitting between
    (`PdfCertificateRenderer._compute_divider_y`).
    """

    segment_length: float
    gap: float
    diamond_half_size: float


@dataclass(frozen=True)
class SealSpec:
    """A drawn gold seal/badge: a thick gold ring, a parchment face, a thin gold inner ring, and
    a centred star/rosette — all vector shapes, no image asset (ADR-021 requires no new image
    assets).

    No text is drawn inside the seal: at this size (radius `outer_radius`) any legible caption
    would either overlap the star or demand a font so small it reads as noise, so the badge
    stays purely graphical (fix-cycle 1, item 2) — a clean design choice over a cluttered one.

    `star_points` is the number of spikes (a 5-pointed star has 5). The star alternates between
    `star_outer_radius` and `star_inner_radius` at each vertex, computed with `cos`/`sin` in
    `pdf_renderer._star_polygon_points`. `star_outer_radius` is kept smaller than
    `inner_ring_radius` so the star sits inside the thin inner ring rather than crossing it.
    """

    y: float
    outer_radius: float
    face_radius: float
    inner_ring_radius: float
    star_points: int
    star_outer_radius: float
    star_inner_radius: float


@dataclass(frozen=True)
class CertificateTemplate:
    """A complete, immutable visual design. One instance = one `template_id`."""

    template_id: str
    page_size: tuple[float, float]
    text_max_width: float  # usable width for centered text = page width - 2 * margin
    palette: Palette
    border: BorderSpec
    corner_ornament: CornerOrnamentSpec
    divider: DividerSpec
    seal: SealSpec
    title: FittedText
    salutation: FixedText
    recipient_name: FittedText
    completion_line: FixedText
    program_name: FittedText
    # "Issued by <issuer> on <date>" — issuer/date are filled in at render time.
    issued_by: FittedText
    issued_by_template: str
    # Signature block (right of center) — text sits *below* its rule, exactly as the original,
    # undecorated design did. Field names kept stable: read directly by existing tests.
    signature_x_offset: float  # horizontal offset from center_x (positive = right of center)
    signature_y: float
    signature_font_size: float
    signature_line_width: float
    signature_line_offset: float  # vertical gap between the rule and `signature_y`
    # Date block (left of center, mirrors the signature block) — the formatted date sits
    # *above* its rule and a "Date" caption sits below it, like a dated-and-signed document.
    date_x_offset: float  # horizontal offset from center_x (negative = left of center)
    date_caption_y: float
    date_caption_font_size: float
    date_line_width: float
    date_line_offset: float  # vertical gap between the rule and `date_caption_y`
    date_value_gap: float  # vertical gap between the rule and the date value above it
    date_value_font_size: float
    # "Certificate No. <number>" — number is filled in at render time.
    footer: FixedText
    footer_template: str


_WIDTH, _HEIGHT = PAGE_SIZE
_MARGIN = 70.0

CLASSIC_V1 = CertificateTemplate(
    template_id="classic_v1",
    page_size=PAGE_SIZE,
    text_max_width=_WIDTH - 2 * _MARGIN,
    palette=CLASSIC_PALETTE,
    border=BorderSpec(outer_inset=28.0, outer_width=2.5, inner_inset=40.0, inner_width=1.0),
    corner_ornament=CornerOrnamentSpec(inset=36.0, size=9.0),
    divider=DividerSpec(segment_length=50.0, gap=14.0, diamond_half_size=5.0),
    seal=SealSpec(
        y=108.0,
        outer_radius=34.0,
        face_radius=26.0,
        inner_ring_radius=21.0,
        star_points=5,
        star_outer_radius=16.0,
        star_inner_radius=6.5,
    ),
    # The title text itself is dynamic content (`CertificateContent.title`), not layout, so only
    # its geometry lives here; the default wording ("Certificate of Completion") lives in the
    # request schema (CLAUDE.md: api holds no business logic, template.py is layout-only data).
    #
    # Fix-cycle 1, item 3: the whole upper block (title..issued_by) was pulled up ~24pt from its
    # original position. That shrinks the previously-oversized blank band between the inner
    # border and the title, and grows the gap between `issued_by` and the seal/date/signature
    # row instead — moving slack from a place that had too much of it to a place that had none,
    # without touching the bottom row's own layout at all.
    title=FittedText(
        max_font_size=30,
        min_font_size=18,
        y=_HEIGHT - 126,
        line_gap=26,
        color=CLASSIC_PALETTE.navy,
        font_role="display_bold",
    ),
    salutation=FixedText(
        text="This is to certify that", font_size=14, y=_HEIGHT - 186, color=CLASSIC_PALETTE.grey
    ),
    # `completion_line` sits 50pt below `recipient_name` (was 60pt): tightened so a one-line name
    # — centred at the midpoint of its own two-line band, 12pt "above" where a two-line name's
    # lower line would sit — doesn't read as a gap to the line below (fix-cycle 1, item 3).
    recipient_name=FittedText(
        max_font_size=36,
        min_font_size=16,
        y=_HEIGHT - 236,
        line_gap=24,
        color=CLASSIC_PALETTE.navy,
        font_role="name",
    ),
    completion_line=FixedText(
        text="has successfully completed",
        font_size=14,
        y=_HEIGHT - 286,
        color=CLASSIC_PALETTE.grey,
    ),
    program_name=FittedText(
        max_font_size=20,
        min_font_size=12,
        y=_HEIGHT - 321,
        line_gap=18,
        color=CLASSIC_PALETTE.navy,
        font_role="display_bold",
    ),
    issued_by=FittedText(
        max_font_size=12, min_font_size=9, y=_HEIGHT - 366, line_gap=14, color=CLASSIC_PALETTE.grey
    ),
    issued_by_template="Issued by {issuer} on {date}",
    signature_x_offset=230.0,
    signature_y=92.0,
    signature_font_size=10,
    signature_line_width=150.0,
    signature_line_offset=16.0,
    date_x_offset=-230.0,
    date_caption_y=92.0,
    date_caption_font_size=8.5,
    date_line_width=150.0,
    date_line_offset=16.0,
    date_value_gap=14.0,
    date_value_font_size=11,
    footer=FixedText(text="", font_size=9, y=50.0, color=CLASSIC_PALETTE.grey),
    footer_template="Certificate No. {number}",
)
