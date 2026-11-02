"""A complete frame represented by a row-major grid of independently matched glyphs."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .cuda_match import exact_u8_search, native_available
from .glyphs import Dictionary, GlyphConfig, feature
from .outline import OutlineRenderer
from .search import exact_search
from .video import Frames, extract_frames, ffmpeg_binary, ui_font


def tile_features(image: Image.Image, columns: int, rows: int, config: GlyphConfig) -> np.ndarray:
    """Keep every source pixel; cells are ordered left-to-right, top-to-bottom."""
    if columns<1 or rows<1 or columns>image.width or rows>image.height:
        raise ValueError("Grid dimensions must be positive and no larger than the source image")
    vectors = []
    for row in range(rows):
        for column in range(columns):
            box = (column*image.width//columns,row*image.height//rows,
                   (column+1)*image.width//columns,(row+1)*image.height//rows)
            # Stretch both the square source cell and glyph ink to the same
            # 32x24 feature grid. The displayed glyph itself uses a square cell.
            vectors.append(feature(image.crop(box),config))
    return np.stack(vectors)


def grid_queries(frames: Frames, columns: int, rows: int, config: GlyphConfig, cache: Path) -> np.ndarray:
    identity = {"version":1,"source":frames.metadata["identity"],"frames":frames.count,
                "columns":columns,"rows":rows}
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
    path = cache / (key+".npy")
    shape = (frames.count,columns*rows,config.feature_width*config.feature_height)
    if path.exists():
        values = np.load(path,allow_pickle=False,mmap_mode="r")
        if values.shape!=shape or values.dtype!=np.uint8:
            raise ValueError(f"Invalid grid feature cache: {path}")
        return values
    cache.mkdir(parents=True,exist_ok=True)
    partial = path.with_suffix(".partial.npy")
    result = np.lib.format.open_memmap(partial,mode="w+",dtype=np.uint8,shape=shape)
    thumbnails = frames.thumbnails()
    for index in range(frames.count):
        result[index] = tile_features(Image.fromarray(thumbnails[index]),columns,rows,config)
        if (index+1)%500==0:
            print(f"grid features: {index+1}/{frames.count}",flush=True)
    result.flush()
    del result
    partial.replace(path)
    return np.load(path,allow_pickle=False,mmap_mode="r")


def match_cells(queries: np.ndarray, candidates: np.ndarray, backend: str = "auto"):
    flat = queries.reshape(-1,queries.shape[-1])
    unique,inverse = np.unique(flat,axis=0,return_inverse=True)
    print(f"grid match: {len(flat):,} cells, {len(unique):,} distinct queries, {len(candidates):,} candidates",flush=True)
    requested = "native" if backend=="auto" and native_available() else backend
    if requested=="native":
        ids,scores,used = exact_u8_search(unique,candidates)
    else:
        ids,scores,used = exact_search(unique,candidates,backend=requested,query_batch=256)
    shape = queries.shape[:-1]
    return ids[inverse].reshape(shape),scores[inverse].reshape(shape),used,len(unique)


def export_grid(ids: np.ndarray, scores: np.ndarray, dictionary: Dictionary, frames: Frames,
                output: Path, columns: int, rows: int) -> np.ndarray:
    if ids.shape!=(frames.count,columns*rows) or scores.shape!=ids.shape:
        raise ValueError("Grid matches do not agree with the frame count and dimensions")
    mapping = {int(g):dictionary.source(int(g))[0] for g in np.unique(ids)}
    codepoints = np.array([[mapping[int(g)] for g in frame] for frame in ids],dtype=np.int32)
    with (output/"frames.jsonl").open("w",encoding="utf-8") as records, (output/"characters.txt").open("w",encoding="utf-8",newline="\n") as text:
        for index,(glyphs,cps,distances) in enumerate(zip(ids,codepoints,scores)):
            sequence = "".join(chr(int(cp)) for cp in cps)
            text.write(sequence+"\n")
            records.write(json.dumps({"frame":index,"time":float(index/frames.fps),"characters":sequence,
                "codepoints":cps.tolist(),"glyph_ids":glyphs.tolist(),"distances":distances.tolist()},ensure_ascii=False)+"\n")
    (output/"timing.json").write_text(json.dumps({"fps":str(frames.fps),"frame_count":frames.count,
        "columns":columns,"rows":rows,"characters_per_frame":columns*rows,"order":"row-major",
        "text_format":"UTF-8; one frame per line, no normalization"},indent=2),encoding="utf-8")
    return codepoints


class GridLegend:
    """Fixed cells pair each selected font glyph with its Unicode code point."""

    def __init__(self, size: tuple[int,int], count: int, fonts_root: Path | None = None):
        sx,sy = size[0]/1280,size[1]/960
        self.sx,self.sy = sx,sy
        self.count = count
        self.columns = max(1,(count+23)//24)
        self.rows = (count+self.columns-1)//self.columns
        self.glyph_size = (max(1,round(18*sx)),max(1,round(18*sy)))
        self.heading = ui_font(max(1,round(13*sx)),fonts_root,monospace=True)
        self.code_font = ui_font(max(1,round(min(11,224/self.columns/10)*sx)),fonts_root,monospace=True)
        self.index_font = ui_font(max(1,round(8*sx)),fonts_root,monospace=True)
        self.positions = [(round((1024+(at//self.rows)*224/self.columns)*sx),
                           round((370+(at%self.rows)*22)*sy)) for at in range(count)]

    def draw(self, canvas: Image.Image, glyph_ids, codepoints, render_glyph):
        if len(glyph_ids)!=self.count or len(codepoints)!=self.count:
            raise ValueError("Legend must contain one glyph and code point per grid cell")
        draw = ImageDraw.Draw(canvas)
        draw.text((round(1024*self.sx),round(356*self.sy)),"CELL / GLYPH / CODE POINT",
                  font=self.heading,fill="#919aa9",anchor="ls")
        for at,((x,y),glyph_id,cp) in enumerate(zip(self.positions,glyph_ids,codepoints)):
            draw.text((x,y+round(9*self.sy)),f"{at+1:02d}",font=self.index_font,fill="#919aa9",anchor="lm")
            # Render the selected face in isolation so combining marks, RTL
            # characters and unsupported UI-font glyphs keep their actual shapes.
            canvas.paste(render_glyph(int(glyph_id)),(x+round(17*self.sx),y))
            draw.text((x+round(40*self.sx),y+round(9*self.sy)),f"U+{int(cp):04X}",
                      font=self.code_font,fill="#f5d76e",anchor="lm")


def render_grid(source: Path, frames: Frames, dictionary: Dictionary, ids: np.ndarray,
                codepoints: np.ndarray, output: Path, columns: int, rows: int, *,
                size: tuple[int,int] = (2560,1920), encoder: str = "libx264",
                fonts_root: Path | None = None, supersample: int = 2):
    if min(size)<480 or any(side%2 for side in size):
        raise ValueError("Video dimensions must be even and at least 480px")
    if ids.shape!=(frames.count,columns*rows) or codepoints.shape!=ids.shape:
        raise ValueError("Grid matches do not agree with the frame count")
    sx,sy = size[0]/1280,size[1]/960
    cell = (int(960*sx)//columns,int(720*sy)//rows)
    if min(cell)<8:
        raise ValueError("Grid cells are too small")
    origin = (round(32*sx),round(104*sy))
    inset_size = (round(224*sx),round(168*sy))
    inset_origin = (round(1024*sx),round(104*sy))
    font = ui_font(max(1,round(18*sx)),fonts_root,monospace=True)
    small = ui_font(max(1,round(13*sx)),fonts_root,monospace=True)
    legend = GridLegend(size,columns*rows,fonts_root)
    outline = OutlineRenderer(dictionary,supersample,dictionary.path/"display-cache")
    glyphs = sorted((int(g) for g in np.unique(ids)),key=lambda g:dictionary.source(g)[1:3])
    try:
        # Group by face so CJK fonts are not repeatedly opened for each new cell.
        for index,glyph_id in enumerate(glyphs):
            outline.render(glyph_id,cell,"stretch")
            outline.render(glyph_id,legend.glyph_size,"fit")
            if (index+1)%250==0:
                print(f"grid display cache: {index+1}/{len(glyphs)}",flush=True)
    finally:
        outline.close()

    @lru_cache(maxsize=128)
    def displayed(glyph_id):
        return outline.render(int(glyph_id),cell,"stretch").convert("RGB")

    @lru_cache(maxsize=256)
    def legend_glyph(glyph_id):
        return outline.render(int(glyph_id),legend.glyph_size,"fit").convert("RGB")

    partial = output.with_name(output.stem+".partial.mp4")
    duration = float(frames.count/frames.fps)
    command = [ffmpeg_binary("ffmpeg"),"-y","-v","error","-nostdin","-f","rawvideo","-pix_fmt","rgb24",
        "-s:v",f"{size[0]}x{size[1]}","-r",str(frames.fps),"-i","pipe:0","-i",str(source),
        "-map","0:v:0","-map","1:a:0?","-c:v",encoder]
    command += ["-preset","medium","-crf","18"] if encoder=="libx264" else ["-preset","p5","-cq","20"]
    command += ["-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-t",f"{duration:.9f}","-movflags","+faststart",str(partial)]
    thumbs = frames.thumbnails()
    digits = len(str(frames.count))
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command,stdin=subprocess.PIPE,stderr=errors)
        try:
            for index,(glyph_ids,cps) in enumerate(zip(ids,codepoints)):
                canvas = Image.new("RGB",size,"black")
                draw = ImageDraw.Draw(canvas)
                draw.text((round(32*sx),round(48*sy)),f"BAD APPLE / {columns*rows} UNICODE / {columns} x {rows}",font=font,fill="#c5cbd5",anchor="ls")
                for at,glyph_id in enumerate(glyph_ids):
                    row,column = divmod(at,columns)
                    canvas.paste(displayed(int(glyph_id)),(origin[0]+column*cell[0],origin[1]+row*cell[1]))
                original = Image.fromarray(thumbs[index]).resize(inset_size,Image.Resampling.LANCZOS).convert("RGB")
                x,y = inset_origin
                draw.rectangle((x-2,y-2,x+inset_size[0]+1,y+inset_size[1]+1),fill="#657187")
                canvas.paste(original,inset_origin)
                draw.text((x,y-round(18*sy)),"ORIGINAL",font=small,fill="#919aa9",anchor="ls")
                draw.text((x,round(306*sy)),f"frame {index+1:0{digits}d}/{frames.count}",font=small,fill="#919aa9",anchor="ls")
                legend.draw(canvas,glyph_ids,cps,legend_glyph)
                process.stdin.write(canvas.tobytes())
                if index==min(3967,frames.count-1):
                    canvas.save(output.with_suffix(".poster.png"))
                if (index+1)%500==0:
                    print(f"grid render: {index+1}/{frames.count}",flush=True)
            process.stdin.close()
            if process.wait():
                errors.seek(0)
                raise RuntimeError(errors.read().decode(errors="replace"))
        except BaseException as exc:
            if process.poll() is None:
                process.kill();process.wait()
            if isinstance(exc,BrokenPipeError):
                errors.seek(0)
                raise RuntimeError(errors.read().decode(errors="replace")) from exc
            raise
        finally:
            if not process.stdin.closed:
                process.stdin.close()
            displayed.cache_clear()
            legend_glyph.cache_clear()
            outline.close()
    partial.replace(output)
    print(f"Grid video: {output}",flush=True)


def run_grid(source: Path, dictionary_path: Path, output: Path, frame_cache: Path, *,
             columns: int = 4, rows: int = 3, backend: str = "auto", fps: str | None = None,
             max_frames: int | None = None, size: tuple[int,int] = (2560,1920),
             encoder: str = "libx264", fonts_root: Path | None = None,
             supersample: int = 2, no_video: bool = False) -> dict:
    if columns<1 or rows<1 or columns>32 or rows>32:
        raise ValueError("Grid dimensions must be in 1..32")
    started = time.monotonic()
    output.mkdir(parents=True,exist_ok=True)
    dictionary = Dictionary(dictionary_path)
    try:
        # Preserve at least the 80x80 source detail per cell used in the 4x3
        # version. An 8x6 grid therefore samples the video at 640x480.
        source_size = (max(320,80*columns),max(240,80*rows))
        frames = extract_frames(source,frame_cache,dictionary.config,fps,max_frames,size=source_size)
        queries = grid_queries(frames,columns,rows,dictionary.config,frame_cache.parent/"grid-features")
        identity = {"schema":2,"source_sha256":frames.metadata["identity"]["source_sha256"],"frames":frames.count,
            "fps":str(frames.fps),"columns":columns,"rows":rows,"normalization":"stretch","backend":backend,
            "feature_source_size":[frames.metadata["width"],frames.metadata["height"]],
            "dictionary_manifest_sha256":hashlib.sha256((dictionary_path/"manifest.json").read_bytes()).hexdigest()}
        match_path = output/"matches.npz"
        manifest_path = output/"matches.json"
        if match_path.exists() and manifest_path.exists() and json.loads(manifest_path.read_text())["identity"]==identity:
            with np.load(match_path,allow_pickle=False) as cached:
                ids,scores = cached["ids"],cached["scores"]
            metadata = json.loads(manifest_path.read_text())
            used,unique = metadata["backend"],metadata["distinct_queries"]
            print("Reusing completed grid matches",flush=True)
        else:
            ids,scores,used,unique = match_cells(queries,dictionary.features("stretch"),backend)
            partial = output/"matches.partial.npz"
            np.savez_compressed(partial,ids=ids,scores=scores)
            partial.replace(match_path)
            manifest_path.write_text(json.dumps({"identity":identity,"backend":used,"distinct_queries":unique},indent=2),encoding="utf-8")
        codepoints = export_grid(ids,scores,dictionary,frames,output,columns,rows)
        summary = dict(identity,backend=used,characters_per_frame=columns*rows,cell_queries=frames.count*columns*rows,
            distinct_queries=unique,dictionary_glyphs=dictionary.count,covered_codepoints=dictionary.manifest["covered_codepoints"],
            unique_characters=int(len(np.unique(codepoints))),unique_glyphs=int(len(np.unique(ids))),mean_distance=float(scores.mean()),
            exact_search=True,source=str(source.resolve()),dictionary=str(dictionary_path.resolve()),
            output_resolution=list(size),encoder=encoder,glyph_renderer="outline",supersample=supersample,
            whitespace_candidates=False,code_points_shown=True,glyph_legend_shown=True,
            frame_cache=str(frames.directory.resolve()))
        if not no_video:
            render_grid(source,frames,dictionary,ids,codepoints,output/f"badapple-unicode-{columns}x{rows}.mp4",
                columns,rows,size=size,encoder=encoder,fonts_root=fonts_root,supersample=supersample)
        summary["elapsed_seconds"] = time.monotonic()-started
        (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
        return summary
    finally:
        dictionary.close()
