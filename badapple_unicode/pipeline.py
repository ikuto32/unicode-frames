"""Compare normalizations, export text/metadata, then encode the complete video."""
from __future__ import annotations

import json
import hashlib
import shutil
import time
import zipfile
from collections import Counter
from dataclasses import asdict
from fractions import Fraction
from itertools import groupby
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .glyphs import Dictionary, MODES, normalize_glyph
from .search import exact_search
from .video import Frames, extract_frames, render_video, ui_font
from .unicode_names import unicode_name


def comparison_image(frames: Frames, dictionary: Dictionary, sample_indices: np.ndarray,
                     matches: dict, output: Path, fonts_root: Path | None):
    # Pick samples with visible structure, spread over the clip, for visual review.
    thumbs = frames.thumbnails()
    variances = np.array([np.std(thumbs[int(i)]) for i in sample_indices])
    structured = np.flatnonzero(variances > 35)
    pool = structured if len(structured) else np.arange(len(sample_indices))
    chosen = pool[np.linspace(0, len(pool)-1, min(8, len(pool)), dtype=int)]
    width, row_height = 1110, 292
    sheet = Image.new("RGB", (width, 54+row_height*len(chosen)), "#0c1018")
    draw = ImageDraw.Draw(sheet)
    font = ui_font(18, fonts_root)
    for x, label in zip([20, 390, 760], ["ORIGINAL", "STRETCH / 4:3", "FIT / KEEP ASPECT"]):
        draw.text((x, 16), label, fill="white", font=font)
    for row, sample in enumerate(chosen):
        index = int(sample_indices[sample])
        y = 54+row*row_height
        image = Image.fromarray(thumbs[index]).resize((320, 240))
        sheet.paste(image.convert("RGB"), (20, y))
        draw.text((20, y+244), f"{float(index/frames.fps):.2f}s / frame {index}", fill="#aab3c1", font=font)
        for x, mode in [(390, "stretch"), (760, "fit")]:
            glyph_id = int(matches[mode]["ids"][sample])
            cp, _, crop = dictionary.glyph(glyph_id)
            glyph = normalize_glyph(crop, (320, 240), mode)
            sheet.paste(glyph.convert("RGB"), (x, y))
            draw.text((x, y+244), f"U+{cp:04X}   d={float(matches[mode]['scores'][sample]):.5f}",
                      fill="#f5d76e", font=font)
    sheet.save(output)


def timestamp(seconds: float) -> str:
    milliseconds = round(seconds*1000)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    whole, fraction = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02},{fraction:03}"


def collapsed_characters(text: str) -> str:
    """Keep one scalar per adjacent equal-character run, preserving all later repeats."""
    return "".join(character for character,_ in groupby(text))


def export_records(records: list[dict], fps, output: Path, mode: str, metric: str):
    # No BOM and no normalization: exactly one Unicode scalar per source frame.
    text = "".join(r["character"] for r in records)
    (output / "characters.txt").write_text(text, encoding="utf-8")
    (output / "characters-collapsed.txt").write_text(collapsed_characters(text),encoding="utf-8")
    (output / "frames.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in records), encoding="utf-8")
    (output / "timing.json").write_text(json.dumps({"fps": str(fps), "frame_count": len(records),
        "normalization": mode, "metric": metric, "encoding": "UTF-8", "one_codepoint_per_frame": True}, indent=2), encoding="utf-8")
    subtitles = []
    start = 0
    while start < len(records):
        end = start+1
        while end < len(records) and records[end]["glyph_id"] == records[start]["glyph_id"]:
            end += 1
        r = records[start]
        subtitles.append(f"{len(subtitles)+1}\n{timestamp(float(start/fps))} --> {timestamp(float(end/fps))}\n{r['unicode']} {r['name']}\n\n")
        start = end
    (output / "characters.srt").write_text("".join(subtitles), encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("player.py"), output / "play_characters.py")
    instructions = ("Unzip all files into one folder and run: python play_characters.py\n"
                    "Python 3.11+; no third-party dependencies. Stop with Ctrl+C.\n"
                    "characters.txt is UTF-8 with exactly one codepoint per frame; do not normalize it.\n"
                    "characters-collapsed.txt keeps one character per consecutive run; it does not use frame timing.\n"
                    "Timing is in timing.json. Terminal font fallback determines glyph appearance.\n")
    with zipfile.ZipFile(output / "characters.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["characters.txt", "characters-collapsed.txt", "timing.json", "play_characters.py"]:
            archive.write(output / name, arcname=name)
        archive.writestr("README.txt", instructions)


def run_pipeline(source: Path, dictionary_path: Path, output: Path, frame_cache: Path,
                 *, normalization: str = "auto", metric: str = "l2", backend: str = "auto",
                 fps: str | None = None, max_frames: int | None = None, sample_count: int = 128,
                 query_batch: int = 128, candidate_batch: int = 8192, fonts_root: Path | None = None,
                 no_video: bool = False, encoder: str = "libx264", size: tuple[int, int] = (1280, 960),
                 glyph_renderer: str = "outline", supersample: int = 2) -> dict:
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    output.mkdir(parents=True, exist_ok=True)
    dictionary = Dictionary(dictionary_path)
    started = time.monotonic()
    try:
        frames = extract_frames(source, frame_cache, dictionary.config, fps, max_frames)
        features = frames.features()
        indices = np.unique(np.linspace(0, frames.count-1, min(sample_count, frames.count), dtype=int))
        comparison = {}
        options = dict(metric=metric, backend=backend, query_batch=query_batch, candidate_batch=candidate_batch)
        for mode in MODES:
            print(f"Compare normalization: {mode}", flush=True)
            ids, scores, used = exact_search(features[indices], dictionary.features(mode), **options)
            comparison[mode] = {"ids": ids, "scores": scores, "backend": used,
                                "mean_distance": float(scores.mean()), "median_distance": float(np.median(scores))}
        selected = min(MODES, key=lambda m: comparison[m]["mean_distance"]) if normalization == "auto" else normalization
        if selected not in MODES:
            raise ValueError(normalization)
        comparison_image(frames, dictionary, indices, comparison, output / "normalization-comparison.png", fonts_root)
        report = {"normalizations": {m: {k: v for k, v in c.items() if k not in {"ids", "scores"}}
                                     for m, c in comparison.items()}, "sample_frames": indices.tolist(),
                  "selected": selected, "selection": "minimum sampled mean distance" if normalization == "auto" else "user override"}
        (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Selected {selected}: " + ", ".join(f"{m}={comparison[m]['mean_distance']:.6f}" for m in MODES), flush=True)
        ids, scores, used = exact_search(features, dictionary.features(selected), **options)
        records = []
        for index, (glyph_id, score) in enumerate(zip(ids, scores)):
            cp, font, _ = dictionary.glyph(int(glyph_id))
            records.append({"frame": index, "time": float(index/frames.fps), "glyph_id": int(glyph_id),
                            "codepoint": cp, "unicode": f"U+{cp:04X}", "character": chr(cp),
                            "name": unicode_name(cp), "font": font, "distance": float(score)})
        export_records(records, frames.fps, output, selected, metric)
        summary = {"source": str(source.resolve()), "frames": len(records), "fps": str(frames.fps),
                   "duration": float(frames.count/frames.fps), "dictionary": str(dictionary_path.resolve()),
                   "dictionary_glyphs": dictionary.count, "covered_codepoints": dictionary.manifest["covered_codepoints"],
                   "missing_codepoints": dictionary.manifest["missing_codepoints"],
                   "normalization": selected, "metric": metric, "backend": used, "exact_search": True,
                   "encoder": None if no_video else encoder,
                   "output_resolution": None if no_video else list(size),
                   "glyph_renderer": None if no_video else glyph_renderer,
                   "supersample": None if no_video else supersample,
                   "source_sha256": frames.metadata["identity"]["source_sha256"],
                   "frame_cache": str(frames.directory.resolve()),
                   "caption_layout": "fixed-columns-unicode-lines-v1",
                   "mean_distance": float(scores.mean()), "unique_characters": len(set(r["codepoint"] for r in records)),
                   "top_characters": [{"unicode": f"U+{cp:04X}", "name": unicode_name(cp), "frames": count}
                                      for cp, count in Counter(r["codepoint"] for r in records).most_common(20)]}
        if not no_video:
            render_video(source, frames, dictionary, records, selected, output / "badapple-unicode.mp4",
                         fonts_root=fonts_root, encoder=encoder, size=size,
                         glyph_renderer=glyph_renderer, supersample=supersample)
        summary["elapsed_seconds"] = time.monotonic() - started
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    finally:
        dictionary.close()


def render_existing(results: Path, output: Path | None, frame_cache: Path, *,
                    source: Path | None = None, dictionary_path: Path | None = None,
                    fonts_root: Path | None = None, size: tuple[int, int] = (2560, 1920),
                    encoder: str = "libx264", glyph_renderer: str = "outline", supersample: int = 2,
                    layout: str = "annotated") -> dict:
    """Re-encode recorded selections without rebuilding or searching the dictionary."""
    started = time.monotonic()
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    timing = json.loads((results / "timing.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (results / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    if not records or len(records) != timing["frame_count"]:
        raise ValueError("Recorded selections disagree with timing.json")
    for index, record in enumerate(records):
        if record["frame"] != index or abs(record["time"] - float(index/Fraction(timing["fps"]))) > 1e-6:
            raise ValueError(f"Recorded frame order or timing is invalid at {index}")
    source = source or Path(summary["source"])
    output = output or results / ("badapple-unicode-glyph-only.mp4" if layout == "glyph-only" else "badapple-unicode-hq.mp4")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    if summary.get("source_sha256", checksum) != checksum:
        raise ValueError("Source video changed since matching")
    dictionary = Dictionary(dictionary_path or Path(summary["dictionary"]))
    try:
        for record in {r["glyph_id"]: r for r in records}.values():
            cp, font_path, _, _ = dictionary.source(record["glyph_id"])
            if cp != record["codepoint"] or font_path != Path(record["font"]):
                raise ValueError("Dictionary does not agree with recorded selections")
        frames = None
        for path in sorted(frame_cache.glob("*/frames.json")):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            identity = metadata.get("identity", {})
            if (identity.get("source_sha256") == checksum and metadata["count"] == len(records)
                    and Fraction(metadata["fps"]) == Fraction(timing["fps"])
                    and identity.get("config") == asdict(dictionary.config)):
                frames = Frames(path.parent, metadata)
                break
        if frames is None:
            if "source_sha256" not in summary:
                raise ValueError("Original frame cache required to verify this older result; restore it or rerun matching")
            frames = extract_frames(source, frame_cache, dictionary.config, timing["fps"], len(records))
        render_video(source, frames, dictionary, records, timing["normalization"], output,
                     fonts_root=fonts_root, encoder=encoder, size=size,
                     glyph_renderer=glyph_renderer, supersample=supersample,layout=layout)
        report = {"video": str(output.resolve()), "source_sha256": checksum, "frames": len(records),
                  "fps": timing["fps"], "output_resolution": list(size), "encoder": encoder,
                  "glyph_renderer": glyph_renderer, "supersample": supersample,
                  "layout": layout, "original_preview": layout == "annotated",
                  "caption_layout": "fixed-columns-unicode-lines-v1" if layout == "annotated" else None,
                  "unicode_blocks_version": "17.0.0" if layout == "annotated" else None,
                  "number_line_domains": ["U+0000..U+10FFFF","current Unicode block"] if layout == "annotated" else [],
                  "reused_matches": str((results / "frames.jsonl").resolve()),
                  "matches_sha256": hashlib.sha256((results / "frames.jsonl").read_bytes()).hexdigest(),
                  "elapsed_seconds": time.monotonic()-started}
        output.with_suffix(".render.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        dictionary.close()
