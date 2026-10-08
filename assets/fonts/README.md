# Optional fonts

Place `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` here to render non-Latin names (e.g. Devanagari,
accented characters beyond Latin-1) correctly.

If these files are absent, the renderer falls back to ReportLab's built-in Helvetica, which only
supports Latin-1 characters. This is a documented limitation (see ADR-006).
