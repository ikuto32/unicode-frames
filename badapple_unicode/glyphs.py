"""Font fallback, exact visual deduplication and reproducible glyph features."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import unicodedata2 as ucd
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageFilter, ImageFont

from .unicode_names import unicode_name

MODES = ("stretch", "fit")


@dataclass(frozen=True)
class GlyphConfig:
    canvas_width: int = 128
    canvas_height: int = 96
    feature_width: int = 32
    feature_height: int = 24
    font_size: int = 128
    blur: float = 0.65

    def __post_init__(self):
        if min(self.canvas_width, self.canvas_height, self.feature_width,
               self.feature_height, self.font_size) < 1 or self.blur < 0:
            raise ValueError("Sizes must be positive; blur must be nonnegative")


def eligible(cp: int) -> bool:
    """Assigned, non-private, non-control characters; actual empty ink is tested later."""
    char = chr(cp)
    return not ucd.category(char).startswith(("C", "Z")) and not char.isspace() and cp != 0xFFFD


def normalize_glyph(crop: Image.Image, size: tuple[int, int], mode: str) -> Image.Image:
    if mode not in MODES:
        raise ValueError(f"Unknown normalization: {mode}")
    if mode == "stretch":
        return crop.resize(size, Image.Resampling.LANCZOS)
    scale = min(size[0] / crop.width, size[1] / crop.height)
    scaled = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                         Image.Resampling.LANCZOS)
    canvas = Image.new("L", size)
    canvas.paste(scaled, ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2))
    return canvas


def feature(image: Image.Image, config: GlyphConfig) -> np.ndarray:
    small = image.convert("L").resize((config.feature_width, config.feature_height), Image.Resampling.LANCZOS)
    small = small.filter(ImageFilter.GaussianBlur(config.blur))
    return np.asarray(small, dtype=np.uint8).reshape(-1)


def ink(font: ImageFont.FreeTypeFont, cp: int) -> Image.Image | None:
    # BASIC renders the isolated codepoint without synthesizing a dotted-circle base.
    # Explicit cmap inspection is required: Pillow itself would return .notdef silently.
    mask = font.getmask(chr(cp), mode="L")
    if mask.size[0] == 0 or mask.size[1] == 0:
        return None
    image = Image.frombytes("L", mask.size, bytes(mask))
    bbox = image.getbbox()
    return image.crop(bbox) if bbox else None


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def font_files(directories: list[Path]) -> list[Path]:
    paths = set()
    for directory in directories:
        if directory.is_file():
            paths.add(directory.resolve())
        elif directory.is_dir():
            paths.update(p.resolve() for p in directory.rglob("*")
                         if p.suffix.lower() in {".otf", ".ttf", ".ttc", ".otc"})
        else:
            raise FileNotFoundError(directory)
    # Deterministic Noto -> other fonts -> Unifont fallback / tie order.
    def priority(path):
        name = path.name.lower()
        return (2 if "unifont" in name else 0 if "noto" in name else 1,
                0 if "sans" in name else 1, str(path))
    return sorted(paths, key=priority)


def face_count(path: Path) -> int:
    if path.suffix.lower() not in {".ttc", ".otc"}:
        return 1
    collection = TTCollection(path, lazy=True)
    count = len(collection.fonts)
    collection.close()
    return count


def build_dictionary(directories: list[Path], target: Path, config: GlyphConfig,
                     codepoints: set[int] | None = None, fallback_only: bool = False) -> None:
    paths = font_files(directories)
    if not paths:
        raise ValueError("No fonts found. Run fetch first or supply --fonts")
    # Fail before an expensive build if a server returned HTML instead of a font.
    for path in paths:
        with path.open("rb") as stream:
            if stream.read(4) not in {b"OTTO", b"\0\1\0\0", b"ttcf", b"true"}:
                raise ValueError(f"Invalid font file: {path}; rerun fetch")
    # A .building directory is never opened as a usable cache after interruption.
    if target.exists():
        raise FileExistsError(f"Dictionary exists: {target}; choose a new --dictionary path")
    work = target.with_name(target.name + ".building")
    if work.exists():
        raise FileExistsError(f"Interrupted build exists: {work}; move it aside or use another output path")
    work.mkdir(parents=True)
    connection = sqlite3.connect(work / "glyphs.sqlite3")
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE fonts (id INTEGER PRIMARY KEY, path TEXT, face INTEGER, sha256 TEXT);
        CREATE TABLE glyphs (id INTEGER PRIMARY KEY, codepoint INTEGER, font_id INTEGER, png BLOB);
        CREATE TABLE aliases (glyph_id INTEGER, codepoint INTEGER, font_id INTEGER);
    """)
    streams = {mode: (work / f"{mode}.u8").open("wb") for mode in MODES}
    seen: dict[bytes, int] = {}
    covered: set[int] = set()
    mapped: set[int] = set()
    stats = Counter()
    errors = []
    started = time.monotonic()
    font_index = 0
    try:
        for file_index, path in enumerate(paths, 1):
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            for face in range(face_count(path)):
                font_index += 1
                tt = TTFont(path, fontNumber=face, lazy=True)
                cmap = {cp: name for cp, name in (tt.getBestCmap() or {}).items()
                        if eligible(cp) and (codepoints is None or cp in codepoints)}
                cmap = {cp: name for cp, name in cmap.items()
                        if name not in {".notdef", ".null", "nonmarkingreturn"} and tt.getGlyphID(name) != 0}
                tt.close()
                connection.execute("INSERT INTO fonts VALUES (?,?,?,?)", (font_index, str(path), face, checksum))
                mapped.update(cmap)
                try:
                    font = ImageFont.truetype(str(path), config.font_size, index=face, layout_engine=ImageFont.Layout.BASIC)
                    tofu = ink(font, 0x10FFFF)
                    tofu_key = None if tofu is None else (tofu.size, tofu.tobytes())
                except (OSError, ValueError) as exc:
                    errors.append({"font": str(path), "face": face, "error": str(exc)})
                    continue
                # Every face variant is considered by default. Fallback-only is an
                # explicit faster option that stops after one successful face per cp.
                for cp in sorted(cmap):
                    if fallback_only and cp in covered:
                        continue
                    stats["attempted_variants"] += 1
                    try:
                        crop = ink(font, cp)
                    except (OSError, ValueError) as exc:
                        stats["render_failures"] += 1
                        if len(errors) < 100:
                            errors.append({"font": str(path), "codepoint": f"U+{cp:04X}", "error": str(exc)})
                        continue
                    if crop is None:
                        stats["empty_variants"] += 1
                        continue
                    if tofu_key == (crop.size, crop.tobytes()):
                        stats["tofu_variants"] += 1
                        continue
                    canvases = [normalize_glyph(crop, (config.canvas_width, config.canvas_height), mode)
                                for mode in MODES]
                    # Dedup on the complete common canvases, BEFORE downsampling and
                    # blur. Similar feature vectors alone are never called identical.
                    digest = hashlib.sha256(b"".join(image.tobytes() for image in canvases)).digest()
                    glyph_id = seen.get(digest)
                    if glyph_id is None:
                        glyph_id = len(seen)
                        seen[digest] = glyph_id
                        connection.execute("INSERT INTO glyphs VALUES (?,?,?,?)",
                                           (glyph_id, cp, font_index, encode_png(crop)))
                        for mode, canvas in zip(MODES, canvases):
                            streams[mode].write(feature(canvas, config).tobytes())
                    else:
                        stats["duplicate_variants"] += 1
                    connection.execute("INSERT INTO aliases VALUES (?,?,?)", (glyph_id, cp, font_index))
                    covered.add(cp)
            connection.commit()
            if file_index % 5 == 0 or file_index == len(paths):
                print(f"glyphs: fonts {file_index}/{len(paths)}, unique {len(seen):,}, "
                      f"covered codepoints {len(covered):,}, {time.monotonic()-started:.1f}s", flush=True)
        if not seen:
            raise ValueError("No visible glyphs survived filtering")
        connection.execute("CREATE INDEX glyph_aliases ON aliases(glyph_id)")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
        for stream in streams.values():
            stream.close()
    eligible_set = {cp for cp in (codepoints if codepoints is not None else range(0x110000)) if eligible(cp)}
    missing = sorted(eligible_set - covered)
    report = {"schema": 1, "unicode_version": ucd.unidata_version, "config": asdict(config),
              "font_files": len(paths), "font_faces": font_index, "glyphs": len(seen),
              "eligible_codepoints": len(eligible_set), "covered_codepoints": len(covered),
              "mapped_but_invisible_or_failed": len(mapped - covered), "missing_codepoints": len(missing),
              "fallback_only": fallback_only, "counts": dict(stats), "font_errors": errors,
              "elapsed_seconds": time.monotonic() - started}
    (work / "missing.tsv").write_text("codepoint\tname\tcategory\n" + "".join(
        f"U+{cp:04X}\t{unicode_name(cp)}\t{ucd.category(chr(cp))}\n" for cp in missing), encoding="utf-8")
    (work / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    work.rename(target)
    print(json.dumps(report, indent=2), flush=True)


class Dictionary:
    def __init__(self, path: Path):
        self.path = path
        self.manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        self.config = GlyphConfig(**self.manifest["config"])
        self.count = self.manifest["glyphs"]
        self.connection = sqlite3.connect(f"{(path / 'glyphs.sqlite3').resolve().as_uri()}?mode=ro", uri=True)

    def features(self, mode: str) -> np.ndarray:
        if mode not in MODES:
            raise ValueError(mode)
        return np.memmap(self.path / f"{mode}.u8", mode="r", dtype=np.uint8,
                         shape=(self.count, self.config.feature_width * self.config.feature_height))

    @lru_cache(maxsize=512)
    def source(self, glyph_id: int) -> tuple[int, Path, int, str]:
        """Original codepoint, font file, collection face and pinned checksum."""
        row = self.connection.execute(
            "SELECT g.codepoint,f.path,f.face,f.sha256 FROM glyphs g JOIN fonts f ON g.font_id=f.id WHERE g.id=?",
            (int(glyph_id),)).fetchone()
        if row is None:
            raise KeyError(glyph_id)
        return row[0], Path(row[1]), row[2], row[3]

    @lru_cache(maxsize=512)
    def glyph(self, glyph_id: int) -> tuple[int, str, Image.Image]:
        row = self.connection.execute(
            "SELECT g.codepoint, f.path, g.png FROM glyphs g JOIN fonts f ON g.font_id=f.id WHERE g.id=?",
            (int(glyph_id),)).fetchone()
        if row is None:
            raise KeyError(glyph_id)
        image = Image.open(io.BytesIO(row[2])).convert("L")
        return row[0], row[1], image

    def close(self):
        self.glyph.cache_clear()
        self.source.cache_clear()
        self.connection.close()
