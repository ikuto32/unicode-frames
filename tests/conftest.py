from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def make_font(path: Path, cmap: dict[int, str]):
    shapes = {
        ".notdef": [(0, 0), (500, 0), (500, 700), (0, 700)],
        "triangle": [(20, 0), (480, 0), (250, 700)],
        "slant": [(0, 0), (130, 0), (500, 700), (370, 700)],
        "same_tofu": [(0, 0), (500, 0), (500, 700), (0, 700)],
        "blank": [],
    }
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(list(shapes))
    glyphs = {}
    for name, points in shapes.items():
        pen = TTGlyphPen(None)
        if points:
            pen.moveTo(points[0])
            for point in points[1:]:
                pen.lineTo(point)
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in shapes})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap(cmap)
    builder.setupNameTable({"familyName": path.stem, "styleName": "Regular", "uniqueFontIdentifier": path.stem,
                           "fullName": path.stem, "psName": path.stem})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


@pytest.fixture
def synthetic_fonts(tmp_path):
    directory = tmp_path / "fonts"
    directory.mkdir()
    make_font(directory / "NotoTest.ttf", {32: "blank", 65: "triangle", 66: "triangle", 67: "blank",
        68: ".notdef", 69: "blank", 70: "same_tofu", 0x1F34E: "slant"})
    make_font(directory / "unifont-test.ttf", {65: "slant", 69: "slant"})
    return directory
