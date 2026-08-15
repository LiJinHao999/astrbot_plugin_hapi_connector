import unittest
from unittest.mock import patch

from render import card_render, font_manager


class _Mask:
    def __init__(self, value: bytes):
        self.size = (1, 1)
        self.value = value

    def __bytes__(self):
        return self.value


class _Font:
    size = 16

    def __init__(self, supported: set[str]):
        self.supported = supported

    def getmask(self, text: str):
        return _Mask(b"g" if text in self.supported else b"?")


class _Draw:
    def __init__(self):
        self.calls = []

    def textbbox(self, _xy, text, *, font, **_kwargs):
        return (0, 0, len(text) * 10, 16)

    def text(self, xy, text, **kwargs):
        self.calls.append((xy, text, kwargs["font"]))


class FontFallbackTests(unittest.TestCase):
    def test_get_font_runs_switches_only_for_missing_glyphs(self):
        primary = _Font(set("AB"))
        fallback = _Font({"𰻞"})

        with patch.object(font_manager, "_load_fallback_fonts", return_value=(fallback,)):
            runs = font_manager.get_font_runs("A𰻞B", primary)

        self.assertEqual(runs, [("A", primary), ("𰻞", fallback), ("B", primary)])

    def test_draw_text_keeps_run_widths_aligned(self):
        primary = _Font(set("AB"))
        fallback = _Font({"𰻞"})
        draw = _Draw()

        with patch.object(font_manager, "_load_fallback_fonts", return_value=(fallback,)):
            card_render._draw_text(draw, (4, 8), "A𰻞B", primary, (0, 0, 0))

        self.assertEqual([call[1] for call in draw.calls], ["A", "𰻞", "B"])
        self.assertEqual([call[0][0] for call in draw.calls], [4, 14, 24])


if __name__ == "__main__":
    unittest.main()
