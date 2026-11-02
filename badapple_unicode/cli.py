from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assets import fetch_assets
from .glyphs import GlyphConfig, build_dictionary
from .pipeline import render_existing, run_pipeline
from .player import play


def parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(v) for v in value.lower().split("x"))
        if width < 1 or height < 1:
            raise ValueError
        return width, height
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected WIDTHxHEIGHT with positive integers") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bad Apple, one closest Unicode scalar per frame")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="Fetch all Noto families, CJK, emoji, Unifont and source video")
    fetch.add_argument("--assets", type=Path, default=Path("assets"))
    fetch.add_argument("--all-styles", action="store_true", help="Include every Noto weight/width (default: Regular per family)")
    fetch.add_argument("--workers", type=int, default=8)
    fetch.add_argument("--no-video", action="store_true")
    fetch.add_argument("--no-jigmo", action="store_true", help="Use only Noto/Unifont; leaves many CJK extensions uncovered")
    build = sub.add_parser("build", help="Render, filter and deduplicate the complete glyph dictionary")
    build.add_argument("--fonts", type=Path, nargs="+", default=[Path("assets/fonts")])
    build.add_argument("--dictionary", type=Path, default=Path("cache/full-dictionary"))
    build.add_argument("--canvas", type=parse_size, default=(128, 96))
    build.add_argument("--feature-size", type=parse_size, default=(32, 24))
    build.add_argument("--font-size", type=int, default=128)
    build.add_argument("--blur", type=float, default=0.65)
    build.add_argument("--fallback-only", action="store_true", help="Keep first successful font for each cp instead of all font variants")
    run = sub.add_parser("run", help="Compare normalizations, match every frame, encode video and export characters")
    run.add_argument("--input", type=Path, default=Path("assets/badapple.mp4"))
    run.add_argument("--dictionary", type=Path, default=Path("cache/full-dictionary"))
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--frame-cache", type=Path, default=Path("cache/frames"))
    run.add_argument("--fonts-root", type=Path, default=Path("assets/fonts"))
    run.add_argument("--normalization", choices=["auto", "stretch", "fit"], default="auto")
    run.add_argument("--metric", choices=["l2", "iou"], default="l2")
    run.add_argument("--backend", choices=["auto", "numpy", "faiss", "cuda"], default="auto")
    run.add_argument("--fps", help="Rational rate, e.g. 30000/1001; default is source rate")
    run.add_argument("--max-frames", type=int)
    run.add_argument("--samples", type=int, default=128)
    run.add_argument("--query-batch", type=int, default=128)
    run.add_argument("--candidate-batch", type=int, default=8192)
    run.add_argument("--no-video", action="store_true")
    run.add_argument("--encoder", choices=["libx264", "h264_nvenc"], default="libx264")
    run.add_argument("--size", type=parse_size, default=(1280, 960))
    run.add_argument("--glyph-renderer", choices=["outline", "cached"], default="outline")
    run.add_argument("--supersample", type=int, choices=range(1,5), default=2)
    render = sub.add_parser("render", help="Re-render existing selections directly from font outlines")
    render.add_argument("--results", type=Path, default=Path("output"))
    render.add_argument("--output", type=Path)
    render.add_argument("--input", type=Path)
    render.add_argument("--dictionary", type=Path)
    render.add_argument("--frame-cache", type=Path, default=Path("cache/frames"))
    render.add_argument("--fonts-root", type=Path, default=Path("assets/fonts"))
    render.add_argument("--size", type=parse_size, default=(2560,1920))
    render.add_argument("--encoder", choices=["libx264", "h264_nvenc"], default="libx264")
    render.add_argument("--glyph-renderer", choices=["outline", "cached"], default="outline")
    render.add_argument("--supersample", type=int, choices=range(1,5), default=2)
    render.add_argument("--layout", choices=["annotated", "glyph-only"], default="annotated",
                        help="glyph-only shows the selected character and audio without preview or captions")
    playback = sub.add_parser("play", help="Play the UTF-8 character sequence in the terminal")
    playback.add_argument("path", type=Path, nargs="?", default=Path("output/characters.txt"))
    playback.add_argument("--fps")
    playback.add_argument("--speed", type=float, default=1)
    grid = sub.add_parser("grid", help="Match a grid of cells to Unicode characters, e.g. 4x3 or 8x6")
    grid.add_argument("--input",type=Path,default=Path("assets/badapple.mp4"))
    grid.add_argument("--dictionary",type=Path,default=Path("cache/full-dictionary"))
    grid.add_argument("--output",type=Path,default=Path("output/grid-4x3"))
    grid.add_argument("--frame-cache",type=Path,default=Path("cache/frames"))
    grid.add_argument("--fonts-root",type=Path,default=Path("assets/fonts"))
    grid.add_argument("--grid",type=parse_size,default=(4,3),metavar="COLSxROWS")
    grid.add_argument("--backend",choices=["auto","native","numpy","faiss","cuda"],default="auto")
    grid.add_argument("--fps")
    grid.add_argument("--max-frames",type=int)
    grid.add_argument("--size",type=parse_size,default=(2560,1920))
    grid.add_argument("--encoder",choices=["libx264","h264_nvenc"],default="libx264")
    grid.add_argument("--supersample",type=int,choices=range(1,5),default=2)
    grid.add_argument("--no-video",action="store_true")
    web = sub.add_parser("web-export", help="Write the static dictionary files used by the browser version")
    web.add_argument("--dictionary", type=Path, default=Path("cache/full-dictionary"))
    web.add_argument("--output", type=Path, default=Path("web/data"))
    web.add_argument("--dims", type=int, default=128)
    web.add_argument("--workers", type=int)
    web.add_argument("--licenses", type=Path, default=Path("assets/licenses"), help="Font license texts from fetch")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            fetch_assets(args.assets, args.all_styles, args.workers, not args.no_video, not args.no_jigmo)
        elif args.command == "build":
            config = GlyphConfig(*args.canvas, *args.feature_size, args.font_size, args.blur)
            build_dictionary(args.fonts, args.dictionary, config, fallback_only=args.fallback_only)
        elif args.command == "run":
            result = run_pipeline(args.input, args.dictionary, args.output, args.frame_cache,
                normalization=args.normalization, metric=args.metric, backend=args.backend,
                fps=args.fps, max_frames=args.max_frames, sample_count=args.samples,
                query_batch=args.query_batch, candidate_batch=args.candidate_batch,
                fonts_root=args.fonts_root, no_video=args.no_video, encoder=args.encoder, size=args.size,
                glyph_renderer=args.glyph_renderer, supersample=args.supersample)
            print(json.dumps(result, indent=2))
        elif args.command == "render":
            result = render_existing(args.results, args.output, args.frame_cache, source=args.input,
                dictionary_path=args.dictionary, fonts_root=args.fonts_root, size=args.size, encoder=args.encoder,
                glyph_renderer=args.glyph_renderer, supersample=args.supersample,layout=args.layout)
            print(json.dumps(result, indent=2))
        elif args.command == "play":
            play(args.path, args.fps, args.speed)
        elif args.command == "grid":
            from .grid import run_grid
            result = run_grid(args.input,args.dictionary,args.output,args.frame_cache,
                columns=args.grid[0],rows=args.grid[1],backend=args.backend,fps=args.fps,max_frames=args.max_frames,
                size=args.size,encoder=args.encoder,fonts_root=args.fonts_root,supersample=args.supersample,no_video=args.no_video)
            print(json.dumps(result,indent=2))
        elif args.command == "web-export":
            from .web_export import export_web
            export_web(args.dictionary, args.output, dims=args.dims, workers=args.workers, licenses=args.licenses)
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
