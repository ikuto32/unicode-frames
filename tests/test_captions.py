from pathlib import Path

import numpy as np
import pytest

from badapple_unicode.captions import CaptionFooter, YELLOW
from badapple_unicode.unicode_blocks import MAX_CODEPOINT, blocks, number_line_position, unicode_block


def record(cp=0x13355,font="NotoSansEgyptianHieroglyphs-Regular.ttf"):
    return {"codepoint":cp,"name":"EGYPTIAN HIEROGLYPH U031","font":font}


def test_official_unicode_17_blocks_and_inclusive_endpoints():
    assert len(blocks()) == 346
    latin = unicode_block(0x41)
    assert (latin.name,latin.start,latin.end) == ("Basic Latin",0,0x7F)
    egypt = unicode_block(0x13355)
    assert (egypt.name,egypt.start,egypt.end) == ("Egyptian Hieroglyphs",0x13000,0x1342F)
    assert unicode_block(egypt.end) == egypt
    assert unicode_block(egypt.end+1) != egypt
    assert unicode_block(0x2FE0) is None
    assert unicode_block(0x327C1).name == "CJK Unified Ideographs Extension J"


def test_number_lines_have_exact_endpoints_and_correct_linear_position():
    assert number_line_position(0,0,MAX_CODEPOINT,144,1244) == 144
    assert number_line_position(MAX_CODEPOINT,0,MAX_CODEPOINT,144,1244) == 1244
    assert number_line_position(150,100,200,144,1244) == 694
    assert number_line_position(65,65,65,144,1244) == 694
    with pytest.raises(ValueError):
        number_line_position(99,100,200,144,1244)


def test_metadata_columns_do_not_move_when_font_name_changes():
    footer = CaptionFooter((1280,960))
    try:
        short = np.asarray(footer.image(record(font="a.ttf"),600,6955))
        long = np.asarray(footer.image(record(font="A-very-long-font-name-"*8+".ttf"),600,6955))
        # Separators, normalization and counter share fixed pixels, regardless
        # of the preceding font-name field's length or truncation.
        np.testing.assert_array_equal(short[90:120,760:1280],long[90:120,760:1280])
        widths = [footer.fonts["code"].getlength(c) for c in "0123456789ABCDEF"]
        assert max(widths)-min(widths) < 0.01
    finally:
        footer.close()


def test_counter_digit_boundaries_do_not_shift_other_footer_fields():
    footer = CaptionFooter((1280,960))
    try:
        before = np.asarray(footer.image(record(),8,6955))
        after = np.asarray(footer.image(record(),9,6955))
        np.testing.assert_array_equal(before[:,:940],after[:,:940])
        np.testing.assert_array_equal(before[120:,:],after[120:,:])
    finally:
        footer.close()


def test_markers_move_only_horizontally_on_fixed_number_lines():
    footer = CaptionFooter((1280,960))
    yellow = np.array([245,215,110])
    try:
        for cp in [0x41,0x13355,0x1F6D0,0x327C1]:
            image = np.asarray(footer.image(record(cp),10,6955))
            for axis_y in [146,193]:
                rows = np.where(np.all(image[axis_y-12:axis_y+6] == yellow,axis=2))[0]
                assert (rows.min(),rows.max()) == (0,17)
    finally:
        footer.close()
