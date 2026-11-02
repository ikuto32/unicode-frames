import json
import sqlite3

import numpy as np
import pytest
from PIL import Image

from badapple_unicode.glyphs import Dictionary, GlyphConfig, build_dictionary, eligible, normalize_glyph
from badapple_unicode.unicode_names import unicode_name


def test_supplementary_algorithmic_names():
    assert unicode_name(0x327C1) == "CJK UNIFIED IDEOGRAPH-327C1"
    assert unicode_name(0x17000) == "TANGUT IDEOGRAPH-17000"


def test_unicode_filter_rejects_controls_spaces_private_unassigned():
    assert all(eligible(cp) for cp in [65, 0x301, 0x1F34E, 0x20000])
    assert not any(eligible(cp) for cp in [0, 9, 32, 0xA0, 0x200B, 0xD800, 0xE000, 0xFFFD, 0x10FFFF])


def test_fit_preserves_aspect_while_stretch_fills_canvas():
    image = Image.new("L", (10, 40), 255)
    stretch = normalize_glyph(image, (128, 96), "stretch")
    fit = normalize_glyph(image, (128, 96), "fit")
    assert stretch.getbbox() == (0, 0, 128, 96)
    assert fit.getbbox() == (52, 0, 76, 96)


def test_cmap_tofu_filter_fallback_and_exact_visual_dedup(synthetic_fonts, tmp_path):
    target = tmp_path / "dictionary"
    build_dictionary([synthetic_fonts], target, GlyphConfig(), codepoints={32,65,66,67,68,69,70,0x1F34E})
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["covered_codepoints"] == 4  # A, B, E (fallback), and supplementary-plane apple
    assert manifest["counts"]["tofu_variants"] == 1
    assert manifest["counts"]["empty_variants"] == 2
    dictionary = Dictionary(target)
    # FreeType autohinting can rasterize the supplementary symbol and Latin slant
    # differently, even though their vector outlines agree. Keep that difference.
    assert dictionary.count == 3
    connection = sqlite3.connect(target / "glyphs.sqlite3")
    aliases = connection.execute("SELECT glyph_id,codepoint FROM aliases").fetchall()
    assert {cp for _, cp in aliases} == {65,66,69,0x1F34E}
    assert dictionary.features("stretch").shape == (3,768)
    a = {glyph_id for glyph_id, cp in aliases if cp == 65}
    b = {glyph_id for glyph_id, cp in aliases if cp == 66}
    assert b <= a
    assert not np.array_equal(dictionary.features("stretch"), dictionary.features("fit"))
    dictionary.close()
    connection.close()
    with pytest.raises(FileExistsError):
        build_dictionary([synthetic_fonts], target, GlyphConfig())


def test_fallback_only_still_tries_later_face_after_blank(synthetic_fonts, tmp_path):
    target = tmp_path / "fallback"
    build_dictionary([synthetic_fonts], target, GlyphConfig(), codepoints={65,69}, fallback_only=True)
    data = json.loads((target / "manifest.json").read_text())
    assert data["covered_codepoints"] == 2
    assert data["counts"]["attempted_variants"] == 3
