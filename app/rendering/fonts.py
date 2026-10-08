"""Optional DejaVu TTF registration, with built-in-PDF-font fallbacks (ADR-006, ADR-017, ADR-021).

No DejaVu fonts ship with this repo (ADR-017): built-in PDF fonts (Latin-1 only) are the default,
with a documented path to Unicode support by dropping the two TTF files into `assets/fonts/`.

ADR-021 moved the default look from Helvetica to the built-in Times family for a more formal,
"engraved" certificate feel, while keeping the DejaVu path for Unicode names/programs.
"""

from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

#: Default font directory, computed from this module's location so it works regardless of cwd.
DEFAULT_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

_DEJAVU_REGULAR_NAME = "DejaVuSans"
_DEJAVU_BOLD_NAME = "DejaVuSans-Bold"


@dataclass(frozen=True)
class FontSet:
    """Named font *roles*, not raw regular/bold slots, so the drawing code reads as intent.

    - `body`: small, regular-weight text (salutation, "has successfully completed", issuer
      line, footer, captions).
    - `body_bold`: the same weight class but bold — used for small accents (the date value).
    - `display_bold`: the large, formal headline weight — title and program name.
    - `name`: the recipient's name specifically. Built-in PDF fonts give us a free
      Times-BoldItalic style with no extra cost, so the name gets its own distinct, more
      ornamental look instead of reusing `display_bold`.
    """

    body: str
    body_bold: str
    display_bold: str
    name: str


def load_fonts(font_dir: Path) -> FontSet:
    """Register DejaVu Sans from `font_dir` if both TTFs are present; otherwise use built-ins.

    Registration is idempotent: ReportLab lets the same font name be registered repeatedly, so
    calling this more than once (e.g. once per renderer instance) is safe.
    """
    regular_path = font_dir / "DejaVuSans.ttf"
    bold_path = font_dir / "DejaVuSans-Bold.ttf"

    if not (regular_path.is_file() and bold_path.is_file()):
        # Times is a built-in PDF font (no registration needed) and reads as a classic,
        # formal "certificate" serif — a plain upgrade over Helvetica for this design.
        return FontSet(
            body="Times-Roman",
            body_bold="Times-Bold",
            display_bold="Times-Bold",
            name="Times-BoldItalic",
        )

    pdfmetrics.registerFont(TTFont(_DEJAVU_REGULAR_NAME, str(regular_path)))
    pdfmetrics.registerFont(TTFont(_DEJAVU_BOLD_NAME, str(bold_path)))
    # DejaVu ships no italic or bold-italic variant. Unicode coverage matters more than the
    # italic flourish, so every "would be bold/italic" role falls back onto DejaVuSans-Bold.
    return FontSet(
        body=_DEJAVU_REGULAR_NAME,
        body_bold=_DEJAVU_BOLD_NAME,
        display_bold=_DEJAVU_BOLD_NAME,
        name=_DEJAVU_BOLD_NAME,
    )
