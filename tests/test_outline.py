from pathlib import Path

import numpy as np
import pytest

from badapple_unicode.glyphs import Dictionary, GlyphConfig, build_dictionary, normalize_glyph
from badapple_unicode.outline import OutlineRenderer


@pytest.fixture
def display_dictionary(synthetic_fonts, tmp_path):
    target = tmp_path / "dictionary"
    build_dictionary([synthetic_fonts], target, GlyphConfig(), codepoints={65})
    dictionary = Dictionary(target)
    yield dictionary
    dictionary.close()


def test_original_outlines_keep_diagonal_edges_sharp_at_large_sizes(display_dictionary):
    dictionary = display_dictionary
    cached = normalize_glyph(dictionary.glyph(0)[2], (960,720), "stretch")
    renderer = OutlineRenderer(dictionary, 2)
    sharp = renderer.render(0, (960,720), "stretch")
    # Inspect a horizontal scan through the triangle: the low-res source has
    # a wide interpolation ramp; directly rasterized geometry has narrow edges.
    def transition_width(image):
        row = np.asarray(image)[360]
        return np.count_nonzero((row > 8) & (row < 247))
    assert transition_width(sharp) < transition_width(cached) / 2
    assert sharp.size == (960,720)
    renderer.close()


def test_outline_rendering_does_not_read_the_low_resolution_glyph(display_dictionary, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Display renderer must not upscale the matching raster")
    monkeypatch.setattr(display_dictionary, "glyph", forbidden)
    renderer = OutlineRenderer(display_dictionary, 2)
    image = renderer.render(0, (640,480), "fit")
    x0,y0,x1,y1 = image.getbbox()
    assert y0 == 0 and y1 == 480
    assert x0 > 100 and x1 < 540
    renderer.close()
    # Restore before Dictionary.close() clears the method cache.
    monkeypatch.undo()


def test_outline_cache_reuses_the_same_final_resolution_image(display_dictionary, tmp_path):
    cache = tmp_path / "display"
    first = OutlineRenderer(display_dictionary, 2, cache)
    expected = first.render(0, (640,480), "stretch")
    first.close()
    second = OutlineRenderer(display_dictionary, 2, cache)
    second.font = lambda *args: (_ for _ in ()).throw(AssertionError("Expected display cache hit"))
    actual = second.render(0, (640,480), "stretch")
    np.testing.assert_array_equal(actual, expected)
    second.close()


def test_changed_font_is_rejected_instead_of_drawing_another_shape(display_dictionary):
    _, path, _, _ = display_dictionary.source(0)
    path.write_bytes(path.read_bytes() + b"changed")
    renderer = OutlineRenderer(display_dictionary)
    with pytest.raises(ValueError, match="Font changed"):
        renderer.render(0, (640,480), "stretch")
    renderer.close()


@pytest.mark.parametrize("supersample", [0,5,1.5])
def test_invalid_supersampling(display_dictionary, supersample):
    with pytest.raises(ValueError, match="supersampling"):
        OutlineRenderer(display_dictionary, supersample)
