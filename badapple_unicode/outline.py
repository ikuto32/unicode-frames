"""Draw original font outlines at the final display resolution, independently of matching."""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path

import aggdraw
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image

from .glyphs import Dictionary, MODES


class OutlinePen(BasePen):
    """BasePen decomposes composites and converts TrueType quadratics to cubics."""
    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.path = aggdraw.Path()

    def _moveTo(self, point):
        self.path.moveto(*point)

    def _lineTo(self, point):
        self.path.lineto(*point)

    def _curveToOne(self, control1, control2, point):
        self.path.curveto(*control1, *control2, *point)

    def _closePath(self):
        self.path.close()


class OutlineRenderer:
    def __init__(self, dictionary: Dictionary, supersample: int = 2, cache: Path | None = None):
        if not isinstance(supersample, int) or not 1 <= supersample <= 4:
            raise ValueError("Glyph supersampling must be an integer from 1 to 4")
        self.dictionary = dictionary
        self.supersample = supersample
        self.cache = cache
        self.fonts = OrderedDict()
        self.verified = set()

    def font(self, path: Path, face: int, checksum: str):
        key = (path, face, checksum)
        if key in self.fonts:
            self.fonts.move_to_end(key)
            return self.fonts[key]
        if (path, checksum) not in self.verified:
            if not path.is_file():
                raise FileNotFoundError(f"Original font needed for outline rendering: {path}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise ValueError(f"Font changed since the dictionary was built: {path}")
            self.verified.add((path, checksum))
        font = TTFont(path, fontNumber=face, lazy=True)
        glyph_set = font.getGlyphSet()
        entry = (font, glyph_set, font.getBestCmap() or {})
        self.fonts[key] = entry
        while len(self.fonts) > 8:
            _, old = self.fonts.popitem(last=False)
            old[0].close()
        return entry

    def render(self, glyph_id: int, size: tuple[int, int], mode: str) -> Image.Image:
        if mode not in MODES or min(size) < 1:
            raise ValueError("Invalid display normalization or size")
        cp, path, face, checksum = self.dictionary.source(glyph_id)
        # Include source identity and algorithm version: matching caches remain unchanged.
        identity = f"outline-v1:{checksum}:{face}:{cp}:{size}:{mode}:{self.supersample}"
        cache_path = None
        if self.cache is not None:
            key = hashlib.sha256(identity.encode()).hexdigest()
            cache_path = self.cache / key[:2] / (key + ".png")
            if cache_path.exists():
                with Image.open(cache_path) as image:
                    if image.size != size:
                        raise ValueError(f"Invalid display cache dimensions: {cache_path}")
                    return image.convert("L")
        _, glyph_set, cmap = self.font(path, face, checksum)
        name = cmap.get(cp)
        if name is None or name == ".notdef":
            raise ValueError(f"U+{cp:04X} missing from original font: {path}")
        glyph = glyph_set[name]
        bounds = BoundsPen(glyph_set)
        glyph.draw(bounds)
        if bounds.bounds is None:
            raise ValueError(f"No drawable outline for U+{cp:04X} in {path}; use --glyph-renderer cached for bitmap-only fonts")
        xmin, ymin, xmax, ymax = bounds.bounds
        if xmax <= xmin or ymax <= ymin:
            raise ValueError(f"Empty outline for U+{cp:04X} in {path}")
        width, height = (value * self.supersample for value in size)
        sx, sy = width / (xmax-xmin), height / (ymax-ymin)
        if mode == "fit":
            sx = sy = min(sx, sy)
        offset_x = (width - (xmax-xmin)*sx) / 2
        offset_y = (height - (ymax-ymin)*sy) / 2
        pen = OutlinePen(glyph_set)
        # Transform the vector geometry BEFORE rasterization. This also keeps
        # extremely thin horizontal/vertical glyphs crisp when stretched to 4:3.
        glyph.draw(TransformPen(pen, (sx, 0, 0, -sy, offset_x-xmin*sx, offset_y+ymax*sy)))
        image = Image.new("L", (width, height), 0)
        drawing = aggdraw.Draw(image)
        drawing.path(pen.path, None, aggdraw.Brush(255))
        drawing.flush()
        if self.supersample > 1:
            image = image.resize(size, Image.Resampling.LANCZOS)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = cache_path.with_suffix(".partial.png")
            image.save(partial, compress_level=1)
            partial.replace(cache_path)
        return image

    def close(self):
        for font, _, _ in self.fonts.values():
            font.close()
        self.fonts.clear()
