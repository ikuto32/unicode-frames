import json
import shutil
import sqlite3
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from badapple_unicode.glyphs import GlyphConfig, build_dictionary, feature
from badapple_unicode.web_export import (COORDINATE_MAX, KIND_BITMAP, KIND_OUTLINE, OP_CLOSE, OP_LINE, OP_MOVE,
                                         encode_outline, export_web, pixel_bitmap)

ROOT = Path(__file__).resolve().parents[1]


def decode_outline(data: bytes):
    """Python mirror of web/js/dictionary.js outlinePath()."""
    i, x, y, ops = 0, 0, 0, []

    def varint():
        nonlocal i
        shift = value = 0
        while True:
            byte = data[i]
            i += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return -(value + 1) // 2 if value & 1 else value // 2

    counts = {0: 1, 1: 1, 2: 2, 3: 3, 4: 0}
    while i < len(data):
        op = data[i]
        i += 1
        points = []
        for _ in range(counts[op]):
            x += varint()
            y += varint()
            points.append((x, y))
        ops.append((op, points))
    return ops


def test_outline_coordinates_span_the_ink_box_with_y_down():
    ops = [(OP_MOVE, ((100, 0),)), (OP_LINE, ((300, 0),)), (OP_LINE, ((200, 500),)), (OP_CLOSE, ())]
    decoded = decode_outline(encode_outline(ops, (100, 0, 300, 500)))
    assert decoded == [(OP_MOVE, [(0, COORDINATE_MAX)]), (OP_LINE, [(COORDINATE_MAX, COORDINATE_MAX)]),
                       (OP_LINE, [(2048, 0)]), (OP_CLOSE, [])]


def test_pixel_bitmap_uses_nonzero_winding_with_holes():
    # 4x4 square with a 2x2 counter-wound hole, Unifont-style unit of 64.
    u = 64
    outer = [(0, 0), (0, 4), (4, 4), (4, 0)]
    inner = [(1, 1), (3, 1), (3, 3), (1, 3)]
    ops = []
    for contour in (outer, inner):
        ops.append((OP_MOVE, ((contour[0][0] * u, contour[0][1] * u),)))
        ops += [(OP_LINE, ((x * u, y * u),)) for x, y in contour[1:]]
        ops.append((OP_CLOSE, ()))
    mask = pixel_bitmap(ops, u)
    assert mask.tolist() == [[1, 1, 1, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 1, 1, 1]]
    assert pixel_bitmap([(OP_MOVE, ((0, 0),)), (OP_LINE, ((32, 64),)), (OP_CLOSE, ())], u) is None


def fake_licenses(path: Path) -> Path:
    for name in ["Noto-OFL.txt", "Unifont-COPYING.txt", "Jigmo-LICENSE.txt", "Jigmo-README.txt", "emoji/OFL.txt"]:
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text("license", encoding="utf-8")
    return path


def test_export_writes_int8_features_and_parseable_glyph_records(synthetic_fonts, tmp_path):
    dictionary = tmp_path / "dictionary"
    build_dictionary([synthetic_fonts], dictionary, GlyphConfig(), codepoints={65, 66, 69, 0x1F34E})
    output = tmp_path / "web"
    export_web(dictionary, output, dims=8, workers=1, licenses=fake_licenses(tmp_path / "licenses"))
    meta = json.loads((output / "meta.json").read_text(encoding="utf-8"))
    count = meta["candidates"]
    assert meta["dims"] == 8 and count == sum(f["rows"] for f in meta["features"])
    assert (output / "features-0.bin").stat().st_size == count * 8
    assert (output / "pca.bin").stat().st_size == (768 + 8 * 768) * 4
    assert (output / "licenses" / "UNICODE-LICENSE.txt").is_file()
    codepoints = np.frombuffer((output / meta["codepoints"]).read_bytes(), dtype=np.uint32)
    assert len(codepoints) == count
    assert any(block[2] == "Basic Latin" for block in meta["blocks"])
    offsets = np.frombuffer((output / "glyph-index.bin").read_bytes(), dtype=np.uint32)
    assert len(offsets) == count + 1 and np.all(np.diff(offsets.astype(np.int64)) > 0)
    data = b"".join((output / f["url"]).read_bytes() for f in meta["glyphFiles"])
    with sqlite3.connect(dictionary / "glyphs.sqlite3") as db:
        expected = dict(db.execute("SELECT id, codepoint FROM glyphs"))
    for glyph_id in range(count):
        record = data[offsets[glyph_id]:offsets[glyph_id + 1]]
        cp, font, kind, name_length, aspect = struct.unpack_from("<IHBBf", record)
        assert cp == expected[glyph_id] == codepoints[glyph_id] and font < len(meta["fonts"]) and aspect > 0
        assert kind in (KIND_OUTLINE, KIND_BITMAP)
        assert record[12:12 + name_length].decode("ascii")
        if kind == KIND_OUTLINE:
            points = [p for _, pts in decode_outline(record[12 + name_length:]) for p in pts]
            assert min(p[0] for p in points) == 0 and max(p[0] for p in points) == COORDINATE_MAX


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_browser_features_match_pillow(tmp_path):
    rng = np.random.default_rng(7)
    images = [rng.integers(0, 256, shape, dtype=np.uint8) for shape in [(240, 320), (60, 80), (24, 32), (48, 64)]]
    gradient = np.tile(np.linspace(0, 255, 320, dtype=np.uint8), (240, 1))
    images.append(gradient)
    expected = [feature(Image.fromarray(image), GlyphConfig()).tolist() for image in images]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"w": i.shape[1], "h": i.shape[0], "pixels": i.reshape(-1).tolist()} for i in images]))
    script = (
        f"import {{ CellFeaturizer }} from {json.dumps((ROOT / 'web/js/features.js').as_uri())};"
        "import { readFileSync } from 'node:fs';"
        "const cases = JSON.parse(readFileSync(process.argv[1], 'utf8'));"
        "console.log(JSON.stringify(cases.map(({ w, h, pixels }) => {"
        "  const out = new Uint8Array(768); new CellFeaturizer(w, h).feature(Uint8Array.from(pixels), w, 0, 0, out, 0);"
        "  return Array.from(out); })));"
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(cases)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected
