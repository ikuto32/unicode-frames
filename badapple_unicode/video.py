"""FFmpeg streaming I/O, timestamp-preserving frame sampling and composition."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .glyphs import Dictionary, GlyphConfig, feature, normalize_glyph
from .outline import OutlineRenderer
from .captions import CaptionFooter


def ffmpeg_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} is required on PATH")
    return found


def probe(path: Path) -> dict:
    result = subprocess.run([ffmpeg_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], check=True, capture_output=True)
    info = json.loads(result.stdout)
    stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"No video stream: {path}")
    rate = stream.get("avg_frame_rate", "0/0")
    if rate == "0/0":
        rate = stream.get("r_frame_rate", "0/0")
    fps = Fraction(rate)
    if fps <= 0:
        raise ValueError(f"Invalid frame rate: {rate}")
    return {"fps": str(fps), "width": stream["width"], "height": stream["height"],
            "duration": float(stream.get("duration", info.get("format", {}).get("duration", 0))),
            "has_audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def read_exact(stream, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            break
        data.extend(part)
    if data and len(data) != size:
        raise RuntimeError(f"Truncated raw frame: {len(data)} / {size} bytes")
    return bytes(data)


@dataclass
class Frames:
    directory: Path
    metadata: dict

    @property
    def count(self):
        return self.metadata["count"]

    @property
    def fps(self):
        return Fraction(self.metadata["fps"])

    def thumbnails(self):
        return np.memmap(self.directory / "frames.u8", mode="r", dtype=np.uint8,
                         shape=(self.count, self.metadata["height"], self.metadata["width"]))

    def features(self):
        return np.load(self.directory / "features.npy", allow_pickle=False)


def extract_frames(source: Path, cache: Path, config: GlyphConfig, fps: str | None = None,
                   max_frames: int | None = None, size: tuple[int, int] = (320, 240)) -> Frames:
    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames must be positive")
    metadata = probe(source)
    rate = Fraction(fps) if fps else Fraction(metadata["fps"])
    if rate <= 0:
        raise ValueError("fps must be positive")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = {"schema": 1, "source_sha256": checksum, "fps": str(rate), "max_frames": max_frames,
                "size": size, "config": asdict(config)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    directory = cache / key
    manifest_path = directory / "frames.json"
    if manifest_path.exists():
        return Frames(directory, json.loads(manifest_path.read_text(encoding="utf-8")))
    directory.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg_binary("ffmpeg"), "-v", "error", "-nostdin", "-i", str(source), "-an",
               "-vf", f"fps={rate},scale={size[0]}:{size[1]}:flags=lanczos,setsar=1,format=gray"]
    if max_frames:
        command += ["-frames:v", str(max_frames)]
    command += ["-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    vectors = []
    # stderr goes to a file, so a full stderr pipe cannot deadlock the raw stream.
    with tempfile.TemporaryFile() as error_log, (directory / "frames.u8").open("wb") as sink:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log)
        try:
            while True:
                raw = read_exact(process.stdout, size[0] * size[1])
                if not raw:
                    break
                sink.write(raw)
                vectors.append(feature(Image.frombytes("L", size, raw), config))
                if len(vectors) % 500 == 0:
                    print(f"extract: {len(vectors)} frames", flush=True)
            status = process.wait()
            if status:
                error_log.seek(0)
                raise RuntimeError(error_log.read().decode(errors="replace"))
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    if not vectors:
        raise ValueError("No frames decoded")
    np.save(directory / "features.npy", np.stack(vectors), allow_pickle=False)
    metadata.update(identity=identity, source=str(source.resolve()), fps=str(rate), count=len(vectors),
                    width=size[0], height=size[1])
    manifest_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"extract: {len(vectors)} frames at {rate} fps", flush=True)
    return Frames(directory, metadata)


def ui_font(size: int, fonts_root: Path | None = None, *, monospace: bool = False) -> ImageFont.ImageFont:
    if fonts_root and fonts_root.exists():
        preferred = list(fonts_root.rglob("NotoSansMono-Regular.ttf" if monospace else "NotoSans-Regular.ttf"))
        if preferred:
            return ImageFont.truetype(str(preferred[0]), size)
    candidates = ([Path("C:/Windows/Fonts/consola.ttf"),Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")]
                  if monospace else [Path("C:/Windows/Fonts/arial.ttf"),Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")])
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def clipped_text(draw, text, xy, font, fill, max_width):
    while draw.textbbox((0, 0), text, font=font)[2] > max_width and len(text) > 3:
        text = text[:-4] + "..."
    draw.text(xy, text, font=font, fill=fill)


def render_video(source: Path, frames: Frames, dictionary: Dictionary, records: list[dict],
                 mode: str, output: Path, *, fonts_root: Path | None = None,
                 size: tuple[int, int] = (1280, 960), encoder: str = "libx264",
                 glyph_renderer: str = "outline", supersample: int = 2,
                 layout: str = "annotated") -> None:
    if len(records) != frames.count:
        raise ValueError("Match count does not equal frame count")
    if size[0] < 640 or size[1] < 480 or size[0] % 2 or size[1] % 2:
        raise ValueError("Video size must be even and at least 640x480")
    if glyph_renderer not in {"outline", "cached"}:
        raise ValueError(f"Unknown glyph renderer: {glyph_renderer}")
    if layout not in {"annotated", "glyph-only"}:
        raise ValueError(f"Unknown video layout: {layout}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partial.mp4")
    duration = float(Fraction(frames.count, 1) / frames.fps)
    command = [ffmpeg_binary("ffmpeg"), "-y", "-v", "error", "-nostdin", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s:v", f"{size[0]}x{size[1]}", "-r", str(frames.fps), "-i", "pipe:0",
               "-i", str(source), "-map", "0:v:0", "-map", "1:a:0?", "-c:v", encoder]
    command += ["-preset", "medium", "-crf", "18"] if encoder == "libx264" else ["-preset", "p5", "-cq", "20"]
    command += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.9f}",
                "-movflags", "+faststart", str(partial)]
    thumbnails = frames.thumbnails() if layout == "annotated" else None
    sx, sy = size[0] / 1280, size[1] / 960
    small_font = ui_font(round(18 * sx), fonts_root) if layout == "annotated" else None
    footer = CaptionFooter(size,fonts_root) if layout == "annotated" else None
    if layout == "glyph-only":
        unit = min(size[0]//4,size[1]//3)
        view_size = (4*unit,3*unit)
        view_origin = ((size[0]-view_size[0])//2,(size[1]-view_size[1])//2)
    else:
        # Keep the matching viewport 4:3 at every output resolution.
        view_height = min(round(648 * sy), round(size[0] * 0.75))
        view_size = (round(view_height * 4 / 3), view_height)
        view_origin = ((size[0] - view_size[0]) // 2, round(60 * sy))
    inset_size = (round(224 * sx), round(168 * sx))
    inset_origin = (size[0] - inset_size[0] - round(26 * sx), round(54 * sy))
    outline = OutlineRenderer(dictionary, supersample, dictionary.path / "display-cache")

    @lru_cache(maxsize=64)
    def displayed(glyph_id):
        if glyph_renderer == "outline":
            return outline.render(glyph_id, view_size, mode).convert("RGB")
        return normalize_glyph(dictionary.glyph(glyph_id)[2], view_size, mode).convert("RGB")

    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=error_log)
        try:
            for index, record in enumerate(records):
                canvas = Image.new("RGB", size, "black")
                canvas.paste(displayed(record["glyph_id"]), view_origin)
                if layout == "annotated":
                    draw = ImageDraw.Draw(canvas)
                    draw.text((round(26*sx), round(18*sy)), "BAD APPLE  /  ONE UNICODE", font=small_font, fill="#9ca3af")
                    original = Image.fromarray(thumbnails[index]).resize(inset_size, Image.Resampling.LANCZOS).convert("RGB")
                    x, y = inset_origin
                    draw.rectangle((x-2, y-2, x+inset_size[0]+1, y+inset_size[1]+1), fill="#6b7280")
                    canvas.paste(original, inset_origin)
                    draw.text((x, y-round(28*sy)), "ORIGINAL", font=small_font, fill="#b0b7c3")
                    # Fixed baselines, fixed columns and monospaced cells prevent
                    # a changing font name or hex digit from shifting other fields.
                    canvas.paste(footer.image(record,index,frames.count,mode),(0,footer.top))
                process.stdin.write(canvas.tobytes())
                if index == min(600, frames.count-1):
                    canvas.save(output.with_suffix(".poster.png"))
                if (index + 1) % 500 == 0:
                    print(f"render: {index+1}/{frames.count}", flush=True)
            process.stdin.close()
            status = process.wait()
            if status:
                error_log.seek(0)
                raise RuntimeError(error_log.read().decode(errors="replace"))
        except BaseException as exc:
            if process.poll() is None:
                process.kill()
                process.wait()
            error_log.seek(0)
            error = error_log.read().decode(errors="replace")
            if isinstance(exc, BrokenPipeError):
                raise RuntimeError(f"FFmpeg encoder failed: {error}") from exc
            raise
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            outline.close()
            if footer is not None:
                footer.close()
            displayed.cache_clear()
    partial.replace(output)
    displayed.cache_clear()
    print(f"Video: {output}", flush=True)
