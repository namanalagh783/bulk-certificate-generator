import re
from datetime import date
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.rendering.base import CertificateContent, RenderError
from app.rendering.fonts import load_fonts
from app.rendering.pdf_renderer import PdfCertificateRenderer, fit_text
from tests.helpers import extract_pdf_text


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces, so comparing extracted PDF text (which
    breaks lines wherever the renderer wrapped them) against the original unwrapped string only
    fails when actual characters are missing, not because of where line breaks fell."""
    return re.sub(r"\s+", " ", text).strip()


def make_content(**overrides) -> CertificateContent:
    defaults = dict(
        certificate_number="CERT-7K2M9QX4PA",
        recipient_name="Ada Lovelace",
        title="Certificate of Completion",
        program_name="Advanced Python Programming",
        issuer_name="Example Academy",
        issue_date=date(2026, 10, 1),
        signatory_name="Grace Hopper",
        signatory_title="Program Director",
    )
    defaults.update(overrides)
    return CertificateContent(**defaults)


def test_output_starts_with_pdf_header():
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content())
    assert data.startswith(b"%PDF")


def test_text_contains_name_program_issuer_number_and_date():
    renderer = PdfCertificateRenderer()
    content = make_content()
    data = renderer.render(content)
    text = extract_pdf_text(data)

    assert content.recipient_name in text
    assert content.program_name in text
    assert content.issuer_name in text
    assert content.certificate_number in text
    assert "1 October 2026" in text


def test_long_name_with_spaces_renders_without_error():
    name = ("Alexandra Montgomery " * 6).strip()  # > 100 chars, has spaces
    assert len(name) >= 100
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(recipient_name=name))
    assert data.startswith(b"%PDF")


def test_long_name_with_no_spaces_renders_without_error():
    name = "A" * 100
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(recipient_name=name))
    assert data.startswith(b"%PDF")


def test_renders_without_signatory():
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(signatory_name=None, signatory_title=None))
    assert data.startswith(b"%PDF")


def test_renders_with_only_signatory_name():
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(signatory_name="Grace Hopper", signatory_title=None))
    text = extract_pdf_text(data)
    assert "Grace Hopper" in text


def test_renders_with_only_signatory_title():
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(signatory_name=None, signatory_title="Program Director"))
    text = extract_pdf_text(data)
    assert "Program Director" in text


def test_custom_title_is_drawn_and_default_title_is_not():
    renderer = PdfCertificateRenderer()
    content = make_content(title="Certificate of Achievement")
    data = renderer.render(content)
    text = extract_pdf_text(data)
    assert "Certificate of Achievement" in text
    assert "Certificate of Completion" not in text


def test_100_char_title_renders_without_error():
    title = ("Certificate of Extraordinary Achievement in Software " * 2)[:100]
    assert len(title) == 100
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(title=title))
    assert data.startswith(b"%PDF")


def _expected_signature_line(renderer: PdfCertificateRenderer) -> tuple[float, float, float, float]:
    """The (x1, y1, x2, y2) the signature rule is drawn at, derived from template fields.

    Both the signature and date blocks draw a gold rule at the same height (by design, for
    visual symmetry), so tests must distinguish them by x-position, not just y.
    """
    template = renderer._template
    center_x = template.page_size[0] / 2
    x = center_x + template.signature_x_offset
    half_width = template.signature_line_width / 2
    line_y = template.signature_y + template.signature_line_offset
    return (x - half_width, line_y, x + half_width, line_y)


def test_signature_line_drawn_when_signatory_present(monkeypatch):
    calls = []
    original_line = canvas.Canvas.line

    def spy_line(self, x1, y1, x2, y2):
        calls.append((x1, y1, x2, y2))
        return original_line(self, x1, y1, x2, y2)

    monkeypatch.setattr(canvas.Canvas, "line", spy_line)
    renderer = PdfCertificateRenderer()
    renderer.render(make_content())

    expected = _expected_signature_line(renderer)
    assert any(
        all(a == pytest.approx(b) for a, b in zip(call, expected, strict=True)) for call in calls
    )


def test_signature_line_not_drawn_when_signatory_absent(monkeypatch):
    calls = []
    original_line = canvas.Canvas.line

    def spy_line(self, x1, y1, x2, y2):
        calls.append((x1, y1, x2, y2))
        return original_line(self, x1, y1, x2, y2)

    monkeypatch.setattr(canvas.Canvas, "line", spy_line)
    renderer = PdfCertificateRenderer()
    renderer.render(make_content(signatory_name=None, signatory_title=None))

    expected = _expected_signature_line(renderer)
    assert not any(
        all(a == pytest.approx(b) for a, b in zip(call, expected, strict=True)) for call in calls
    )


def test_long_program_name_renders_without_error():
    program = ("Comprehensive Professional Certification Program in Applied Data Science " * 3)[
        :200
    ]
    renderer = PdfCertificateRenderer()
    data = renderer.render(make_content(program_name=program))
    assert data.startswith(b"%PDF")


def test_200_char_program_name_is_never_truncated():
    # Regression for fix-cycle 1 item 1: the old wrap-to-2-lines helper silently dropped any
    # words past the 2nd line. 200 chars is the schema's max for `program_name`.
    program = ("Comprehensive Professional Certification Program in Applied Data Science " * 3)[
        :200
    ]
    renderer = PdfCertificateRenderer()
    text = _normalize(extract_pdf_text(renderer.render(make_content(program_name=program))))
    assert program in text


def test_200_char_issuer_name_is_never_truncated():
    # The issuer is embedded inside the longer "Issued by ... on ..." line, which is where the
    # original truncation was most visible (it also carries the formatted date).
    issuer = ("Example Academy for Advanced Professional and Continuing Education Studies " * 3)[
        :200
    ]
    renderer = PdfCertificateRenderer()
    text = _normalize(extract_pdf_text(renderer.render(make_content(issuer_name=issuer))))
    assert issuer in text
    assert "1 October 2026" in text


def test_100_char_name_is_never_truncated():
    name = ("Alexandra Montgomery " * 6).strip()[:100]
    renderer = PdfCertificateRenderer()
    text = _normalize(extract_pdf_text(renderer.render(make_content(recipient_name=name))))
    assert name in text


def test_100_char_title_is_never_truncated():
    title = ("Certificate of Extraordinary Achievement in Software " * 2)[:100]
    renderer = PdfCertificateRenderer()
    text = _normalize(extract_pdf_text(renderer.render(make_content(title=title))))
    assert title in text


def test_wide_glyph_program_name_is_never_truncated():
    # Repeated capital "W"s are about as wide, per character, as Latin text gets — a stress
    # case for any shrink-to-fit logic that might still drop a line under the right width.
    program = " ".join(["WWWWWWWWWW"] * 18)[:200]
    renderer = PdfCertificateRenderer()
    text = _normalize(extract_pdf_text(renderer.render(make_content(program_name=program))))
    assert program in text


def test_issued_by_lowest_line_stays_above_the_seal():
    """Regression guard for fix-cycle 1 item 1's collision requirement: however many lines the
    (now-unbounded) `issued_by` wrap needs for worst-case input, the lowest one must still sit
    above the seal.

    Computed analytically from the same formula `_draw_fitted` uses (`spec.y` plus a per-line
    offset of `((n - 1) / 2 - index) * line_gap`), so this fails the moment either the template's
    numbers or that formula regress, without needing to spy on specific draw calls.
    """
    issuer = ("Example Academy for Advanced Professional and Continuing Education Studies " * 3)[
        :200
    ]
    renderer = PdfCertificateRenderer()
    template = renderer._template
    spec = template.issued_by
    text = template.issued_by_template.format(issuer=issuer, date="1 October 2026")
    font = getattr(renderer._fonts, spec.font_role)

    lines, _size = fit_text(
        text, font, spec.max_font_size, spec.min_font_size, template.text_max_width
    )
    lowest_line_y = spec.y + ((len(lines) - 1) / 2 - (len(lines) - 1)) * spec.line_gap

    seal = template.seal
    seal_top = seal.y + seal.outer_radius
    assert lowest_line_y > seal_top


def test_library_failure_is_wrapped_in_render_error(monkeypatch):
    def boom(self, *args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(canvas.Canvas, "drawCentredString", boom)
    renderer = PdfCertificateRenderer()
    with pytest.raises(RenderError):
        renderer.render(make_content())


def test_uses_builtin_times_fonts_when_font_dir_has_no_dejavu(tmp_path: Path):
    fonts = load_fonts(tmp_path)
    assert fonts.body == "Times-Roman"
    assert fonts.body_bold == "Times-Bold"
    assert fonts.display_bold == "Times-Bold"
    assert fonts.name == "Times-BoldItalic"


def test_seal_is_drawn(monkeypatch):
    calls = []
    original_circle = canvas.Canvas.circle

    def spy_circle(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original_circle(self, *args, **kwargs)

    monkeypatch.setattr(canvas.Canvas, "circle", spy_circle)
    renderer = PdfCertificateRenderer()
    renderer.render(make_content())

    # The seal draws 3 concentric circles (gold ring, parchment face, thin gold inner ring);
    # nothing else in the renderer calls `circle`, so the count confirms the redesigned seal.
    assert len(calls) == 3


def test_seal_draws_no_text():
    # Fix-cycle 1 item 2: the seal's tiny caption used to overlap the star. It was removed
    # rather than relocated, so "CERTIFIED" must never appear in a rendered certificate.
    renderer = PdfCertificateRenderer()
    text = extract_pdf_text(renderer.render(make_content()))
    assert "CERTIFIED" not in text


def test_background_is_filled_with_parchment(monkeypatch):
    calls = []
    original_rect = canvas.Canvas.rect

    def spy_rect(self, x, y, width, height, *args, **kwargs):
        calls.append((x, y, width, height))
        return original_rect(self, x, y, width, height, *args, **kwargs)

    monkeypatch.setattr(canvas.Canvas, "rect", spy_rect)
    renderer = PdfCertificateRenderer()
    renderer.render(make_content())

    width, height = renderer._template.page_size
    assert any(call == (0, 0, pytest.approx(width), pytest.approx(height)) for call in calls)


def test_render_is_deterministic():
    renderer = PdfCertificateRenderer()
    content = make_content()
    first = renderer.render(content)
    second = renderer.render(content)
    assert first == second


def _worst_case_content() -> CertificateContent:
    """Every field at (or near) its schema max length, in the widest glyph available ("W"), to
    stress the layout as hard as realistic input allows — this is what pushed the title onto a
    2nd line and shrank the divider/salutation gap in fix-cycle 2."""
    return make_content(
        recipient_name=" ".join(["WWWWW"] * 10)[:100],
        title=" ".join(["WWWWW"] * 10)[:100],
        program_name=" ".join(["WWWWWWWWWW"] * 18)[:200],
        issuer_name=" ".join(["WWWWWWWWWW"] * 18)[:200],
    )


@pytest.mark.parametrize(
    "content", [make_content(), _worst_case_content()], ids=["normal", "worst"]
)
def test_layout_bands_do_not_overlap_and_stay_inside_the_border(content):
    """Regression for fix-cycle 2 item 1 (the divider overlapping the salutation) and a guard
    against this whole class of bug: build every element's vertical band from
    `PdfCertificateRenderer.layout_bands` (the renderer's own layout math, not a hand-copied
    duplicate of it) and check none of them overlap, for both ordinary and worst-case content.
    """
    renderer = PdfCertificateRenderer()
    bands = renderer.layout_bands(content)

    _, height = renderer._template.page_size
    inner_inset = renderer._template.border.inner_inset
    border_top = height - inner_inset
    border_bottom = inner_inset

    for name, top, bottom in bands:
        assert bottom <= top, f"{name} has a non-positive height"
        assert top <= border_top, f"{name} crosses the top border"
        assert bottom >= border_bottom, f"{name} crosses the bottom border"

    # Sorted top-to-bottom, each band's bottom must clear the next band's top.
    ordered = sorted(bands, key=lambda band: band[1], reverse=True)
    for (upper_name, _upper_top, upper_bottom), (lower_name, lower_top, _lower_bottom) in zip(
        ordered, ordered[1:], strict=False
    ):
        assert upper_bottom > lower_top, f"{upper_name} overlaps {lower_name}"


def test_fit_text_shrinks_before_wrapping():
    # A short name fits on one line at the max size.
    lines, size = fit_text("Ada Lovelace", "Helvetica", max_size=36, min_size=16, max_width=400)
    assert lines == ["Ada Lovelace"]
    assert size == 36


def test_fit_text_shrinks_past_min_size_without_dropping_text():
    # At `min_size` (16), this text needs more than 2 lines to fit `max_width` (120) at all, so
    # `fit_text` must keep shrinking past `min_size` down to its floor rather than truncating.
    long_name = "Alexandra Montgomery Featherstonehaugh-Wellington the Third of Something"
    lines, size = fit_text(long_name, "Helvetica", max_size=36, min_size=16, max_width=120)
    assert size < 16  # proof it shrank past min_size looking for a 2-line fit
    assert " ".join(lines) == long_name  # every word is present, in order — nothing dropped


def test_fit_text_breaks_single_word_by_characters_without_dropping_text():
    long_word = "A" * 80
    lines, size = fit_text(long_word, "Helvetica", max_size=36, min_size=16, max_width=120)
    assert size < 16
    assert "".join(lines) == long_word  # every character is present — nothing dropped
