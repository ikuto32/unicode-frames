"""Export the glyph dictionary as static files for the browser version.

Matching features are reduced with PCA and stored as int8 with one global
scale, so squared distances stay ordinary integer L2 distances on the GPU.
Glyph shapes are exported per candidate and fetched by byte range on demand.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import time
from concurrent.futures import ProcessPoolExecutor
from importlib.resources import files
from pathlib import Path

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

from .glyphs import Dictionary
from .unicode_blocks import blocks
from .unicode_names import unicode_name

SCHEMA = 1
COORDINATE_MAX = 4095
FEATURE_CHUNK_ROWS = 1 << 17
GLYPH_FILE_BYTES = 20 << 20  # Cloudflare Pages accepts files up to 25 MiB
KIND_OUTLINE, KIND_BITMAP, KIND_PNG = 0, 1, 2
OP_MOVE, OP_LINE, OP_QUAD, OP_CUBIC, OP_CLOSE = range(5)


def font_license(path: str) -> str:
    name = path.lower()
    if "unifont" in name:
        return "Unifont"
    if "jigmo" in name:
        return "Jigmo"
    return "Noto"


def principal_components(features: np.ndarray, dims: int, sample: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    rows = np.sort(rng.choice(len(features), min(sample, len(features)), replace=False))
    data = np.asarray(features[rows], dtype=np.float64)
    mean = data.mean(0)
    values, vectors = np.linalg.eigh(np.cov((data - mean).T))
    order = np.argsort(values)[::-1][:dims]
    vectors = vectors[:, order]
    # Fix the arbitrary eigenvector sign so repeated exports are byte-identical.
    vectors *= np.where(vectors[np.abs(vectors).argmax(0), range(dims)] < 0, -1, 1)
    explained = float(values[order].sum() / values.sum())
    return mean.astype(np.float32), vectors.T.astype(np.float32), explained


def zigzag(value: int, out: bytearray):
    value = (value << 1) ^ (value >> 63)
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)


class RecordingPen(BasePen):
    """BasePen decomposes components and splits TrueType quadratic runs into single segments."""
    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.ops = []

    def _moveTo(self, p):
        self.ops.append((OP_MOVE, (p,)))

    def _lineTo(self, p):
        self.ops.append((OP_LINE, (p,)))

    def _qCurveToOne(self, p1, p2):
        self.ops.append((OP_QUAD, (p1, p2)))

    def _curveToOne(self, p1, p2, p3):
        self.ops.append((OP_CUBIC, (p1, p2, p3)))

    def _closePath(self):
        self.ops.append((OP_CLOSE, ()))

    _endPath = _closePath


def encode_outline(ops, bounds) -> bytes:
    x0, y0, x1, y1 = bounds
    sx = COORDINATE_MAX / max(x1 - x0, 1e-9)
    sy = COORDINATE_MAX / max(y1 - y0, 1e-9)
    out = bytearray()
    px = py = 0
    for op, points in ops:
        out.append(op)
        for x, y in points:
            # Y grows downward in the browser; coordinates span the ink box.
            qx = min(COORDINATE_MAX, max(0, round((x - x0) * sx)))
            qy = min(COORDINATE_MAX, max(0, round((y1 - y) * sy)))
            zigzag(qx - px, out)
            zigzag(qy - py, out)
            px, py = qx, qy
    return bytes(out)


def contours(ops):
    current = []
    for op, points in ops:
        if op == OP_MOVE:
            if current:
                yield current
            current = [points[0]]
        elif op == OP_CLOSE:
            if current:
                yield current
            current = []
        else:
            current.extend(points)
    if current:
        yield current


def pixel_bitmap(ops, unit: float):
    """Exact nonzero-winding raster of a rectilinear pixel outline (Unifont), else None."""
    if not ops or any(op in (OP_QUAD, OP_CUBIC) for op, _ in ops):
        return None
    shapes = []
    for contour in contours(ops):
        grid = [(x / unit, y / unit) for x, y in contour]
        if any(abs(x - round(x)) > 1e-6 or abs(y - round(y)) > 1e-6 for x, y in grid):
            return None
        shapes.append([(round(x), round(y)) for x, y in grid])
    xs = [x for shape in shapes for x, _ in shape]
    ys = [y for shape in shapes for _, y in shape]
    left, top = min(xs), max(ys)
    width, height = max(xs) - left, top - min(ys)
    if not 0 < width <= 255 or not 0 < height <= 255:
        return None
    winding = np.zeros((height, width), dtype=np.int32)
    centers_x = np.arange(width) + left + 0.5
    centers_y = top - np.arange(height) - 0.5
    for shape in shapes:
        for (ax, ay), (bx, by) in zip(shape, shape[1:] + shape[:1]):
            if ax != bx and ay != by:
                return None
            if ax == bx and ay != by:
                # A leftward ray from each pixel center crosses this vertical edge.
                rows = (centers_y > min(ay, by)) & (centers_y < max(ay, by))
                winding[np.ix_(rows, centers_x < ax)] += 1 if by > ay else -1
    mask = winding != 0
    if not mask.any():
        return None
    rows, columns = np.nonzero(mask)
    return mask[rows.min():rows.max() + 1, columns.min():columns.max() + 1]


def encode_record(cp: int, font_index: int, kind: int, aspect: float, payload: bytes) -> bytes:
    name = unicode_name(cp).encode("ascii")
    return struct.pack("<IHBBf", cp, font_index, kind, len(name), aspect) + name + payload


def export_glyphs(task):
    """Encode one font's candidates; run in worker processes."""
    font_path, face, font_index, items = task
    font = TTFont(font_path, fontNumber=face, lazy=True)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    unit = font["head"].unitsPerEm / 16 if font_license(font_path) == "Unifont" else None
    results = []
    failures = 0
    for glyph_id, cp in items:
        record = None
        name = cmap.get(cp)
        if name and name != ".notdef":
            glyph = glyph_set[name]
            pen = RecordingPen(glyph_set)
            glyph.draw(pen)
            bounds_pen = BoundsPen(glyph_set)
            glyph.draw(bounds_pen)
            bounds = bounds_pen.bounds
            if bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                mask = pixel_bitmap(pen.ops, unit) if unit else None
                if mask is not None:
                    height, width = mask.shape
                    payload = bytes((width, height)) + np.packbits(mask, axis=1).tobytes()
                    record = encode_record(cp, font_index, KIND_BITMAP, width / height, payload)
                else:
                    aspect = (bounds[2] - bounds[0]) / (bounds[3] - bounds[1])
                    record = encode_record(cp, font_index, KIND_OUTLINE, aspect, encode_outline(pen.ops, bounds))
        if record is None:
            # Bitmap-only or broken outlines: the parent fills in the matching ink crop.
            failures += 1
            record = encode_record(cp, font_index, KIND_PNG, 1.0, b"")
        results.append((glyph_id, record))
    font.close()
    return results, failures


def copy_licenses(licenses: Path, output: Path):
    """The glyph files contain outlines derived from the fonts, so their licenses travel with them."""
    target = output / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    names = {"Noto-OFL.txt": "Noto-OFL.txt", "Unifont-COPYING.txt": "Unifont-COPYING.txt",
             "Jigmo-LICENSE.txt": "Jigmo-LICENSE.txt", "Jigmo-README.txt": "Jigmo-README.txt",
             "emoji/OFL.txt": "NotoEmoji-OFL.txt"}
    for source, name in names.items():
        if not (licenses / source).is_file():
            raise FileNotFoundError(f"Font license missing: {licenses / source}; run fetch first")
        shutil.copyfile(licenses / source, target / name)
    (target / "UNICODE-LICENSE.txt").write_text(
        files("badapple_unicode").joinpath("data/UNICODE-LICENSE.txt").read_text(encoding="utf-8"), encoding="utf-8")


def export_web(dictionary_path: Path, output: Path, *, dims: int = 128, sample: int = 120_000,
               workers: int | None = None, licenses: Path = Path("assets/licenses")) -> dict:
    started = time.perf_counter()
    dictionary = Dictionary(dictionary_path)
    config = dictionary.config
    output.mkdir(parents=True, exist_ok=True)
    copy_licenses(licenses, output)
    features = dictionary.features("stretch")
    count, width = features.shape
    print(f"web-export: PCA {width}->{dims} on {min(sample, count):,} of {count:,} candidates", flush=True)
    mean, components, explained = principal_components(features, dims, sample)
    projected = np.empty((count, dims), dtype=np.float32)
    for start in range(0, count, 65536):
        block = np.asarray(features[start:start + 65536], dtype=np.float32)
        projected[start:start + len(block)] = (block - mean) @ components.T
    scale = float(np.abs(projected).max() / 127)
    quantized = np.clip(np.round(projected / scale), -127, 127).astype(np.int8)
    del projected
    (output / "pca.bin").write_bytes(mean.tobytes() + components.tobytes())
    feature_files = []
    for number, start in enumerate(range(0, count, FEATURE_CHUNK_ROWS)):
        name = f"features-{number}.bin"
        block = quantized[start:start + FEATURE_CHUNK_ROWS]
        (output / name).write_bytes(block.tobytes())
        feature_files.append({"url": name, "rows": len(block)})

    rows = dictionary.connection.execute(
        "SELECT g.id, g.codepoint, f.id, f.path, f.face FROM glyphs g JOIN fonts f ON g.font_id=f.id ORDER BY g.id").fetchall()
    font_rows = sorted({(font_id, path, face) for _, _, font_id, path, face in rows})
    font_index = {font_id: index for index, (font_id, _, _) in enumerate(font_rows)}
    tasks = []
    by_font = {}
    for glyph_id, cp, font_id, _, _ in rows:
        by_font.setdefault(font_id, []).append((glyph_id, cp))
    for font_id, path, face in font_rows:
        items = by_font[font_id]
        for start in range(0, len(items), 4000):
            tasks.append((path, face, font_index[font_id], items[start:start + 4000]))
    records: list[bytes | None] = [None] * count
    failures = 0
    done = 0
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for results, failed in pool.map(export_glyphs, tasks):
            for glyph_id, record in results:
                records[glyph_id] = record
            failures += failed
            done += len(results)
            print(f"web-export: glyphs {done:,}/{count:,}", flush=True)
    if failures:
        # PNG fallbacks need the dictionary connection, which stays in this process.
        for glyph_id, record in enumerate(records):
            cp, font, kind, name_length, aspect = struct.unpack_from("<IHBBf", record)
            if kind == KIND_PNG:
                image = dictionary.glyph(glyph_id)[2]
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                records[glyph_id] = encode_record(cp, font, KIND_PNG, image.width / image.height, buffer.getvalue())
    offsets = np.zeros(count + 1, dtype=np.uint32)
    glyph_files = []
    handle = None
    position = 0
    for glyph_id, record in enumerate(records):
        if handle is None or handle.tell() + len(record) > GLYPH_FILE_BYTES:
            if handle:
                handle.close()
            name = f"glyphs-{len(glyph_files)}.bin"
            glyph_files.append({"url": name, "start": position})
            handle = (output / name).open("wb")
        offsets[glyph_id] = position
        handle.write(record)
        position += len(record)
    offsets[count] = position
    handle.close()
    for item, end in zip(glyph_files, [f["start"] for f in glyph_files[1:]] + [position]):
        item["bytes"] = end - item["start"]
    (output / "glyph-index.bin").write_bytes(offsets.tobytes())
    # Code point per candidate: lets the browser filter by Unicode block before fetching any glyph.
    codepoints = np.array([cp for _, cp, _, _, _ in rows], dtype=np.uint32)
    (output / "codepoints.bin").write_bytes(codepoints.tobytes())

    meta = {
        "schema": SCHEMA,
        # Changes whenever the binary files change, so browsers drop stale cached copies.
        "build": hashlib.sha256((output / "features-0.bin").read_bytes()[:1 << 20] + offsets.tobytes()
                                 + codepoints.tobytes()).hexdigest()[:16],
        "unicodeVersion": dictionary.manifest["unicode_version"],
        "candidates": count,
        "coveredCodepoints": dictionary.manifest["covered_codepoints"],
        "featureWidth": config.feature_width, "featureHeight": config.feature_height, "blur": config.blur,
        "normalization": "stretch",
        "dims": dims, "scale": scale, "explainedVariance": explained,
        "pca": "pca.bin", "features": feature_files,
        "glyphIndex": "glyph-index.bin", "codepoints": "codepoints.bin", "glyphFiles": glyph_files, "coordinateMax": COORDINATE_MAX,
        "fonts": [{"name": Path(path).stem, "license": font_license(path)} for _, path, _ in font_rows],
        "blank": {"codepoint": 0x20, "name": "SPACE"},
        "blocks": [[block.start, block.end, block.name] for block in blocks()],
        "pngFallbacks": failures,
    }
    (output / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    dictionary.close()
    summary = {"candidates": count, "dims": dims, "explained_variance": round(explained, 4),
               "feature_bytes": int(quantized.nbytes), "glyph_bytes": int(position),
               "png_fallbacks": failures, "seconds": round(time.perf_counter() - started, 1)}
    print(json.dumps(summary, indent=2), flush=True)
    return summary
