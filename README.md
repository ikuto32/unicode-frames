# Bad Apple!! / One Unicode character per frame

**English** | [日本語](README.ja.md)

**Web version: https://unicode-frames.ikutotest.xyz/** (any video, nothing to install)

This tool picks, for every frame of Bad Apple!!, the **single real Unicode character** that looks most like it, by exhaustive search over about 410,000 glyph candidates, and turns the result into a video. The chosen character fills the center, the original video plays in the top-right corner, and the bottom shows `U+XXXX CHARACTER NAME` and where the character sits in Unicode. The audio is taken from the source video.

- 4 columns × 3 rows, 12 characters per frame: [docs/GRID-4X3.md](docs/GRID-4X3.md) (Japanese)
- 8 columns × 6 rows, 48 characters per frame: [docs/GRID-8X6.md](docs/GRID-8X6.md) (Japanese)
- Web version (any video, any number of characters): [web/](web/) — see "Web version" below

## Web version

[web/](web/) is a static site that makes videos in the same style from any video. It is hosted on Cloudflare. Videos are never uploaded: analysis and export both run in the browser. The UI is in English (default) and Japanese.

- Choose how many characters fill the screen, as columns × rows (1×1 to 64×48). Each cell is matched independently.
- Choose which Unicode blocks may be used. There are presets (ASCII, Latin, Kana, CJK ideographs, box and block shapes, Braille, emoji) and a filter by name or code point. The selection is remembered in the browser. Whether whitespace may be used can also be toggled, and that change applies without re-analyzing.
- The settings step shows a preview: one frame, at a position you choose, matched with the current settings. It updates as soon as the character count or blocks change.
- The original frame and the glyphs in use (code point, block, character name, font, number lines) can each be shown or hidden.
- Export is MP4 via WebCodecs (H.264 / H.265 / VP9 / AV1). Audio can be included or removed. When included, it is copied as-is if MP4 can hold its format, and re-encoded to AAC or Opus otherwise. The characters can also be saved as `.txt` (one frame per line).

Matching uses the same 410,510 glyphs as the command-line tool. The features are the same 32×24 with a 0.65 blur; Pillow's Lanczos resize and GaussianBlur are ported down to their rounding (tests confirm pixel-for-pixel agreement with Pillow). To make the data small enough to serve, the 768-dimensional features are reduced to 128 dimensions with PCA and stored as int8 with one global scale (52 MB, keeping 91.3% of the variance). WebGPU int8 dot products compare every candidate, and ties go to the candidate that comes first in the dictionary. Without WebGPU, Web Workers do the same computation. On Bad Apple with one character per frame (6,955 frames), 81.0% of frames get exactly the same character as the command-line tool's 768-dimensional exhaustive search. Where they differ, the chosen character's distance is on average only 0.6% above the best one.

Block selection is a bit mask built from each candidate's code point (`data/codepoints.bin`); the GPU search skips candidates outside the mask. Glyph outlines are stored per candidate in `data/glyphs-*.bin`. Unifont glyphs are stored as bitmaps, keeping their pixel shapes exactly.

When the site opens, it first shows how much data it needs (about 260 MB) and the browser's free storage, and asks for permission. After you agree, it downloads all the data and keeps it in the browser (Cache Storage); later visits use the stored copy without asking. The stored data can be deleted from the link at the bottom of the page. Glyphs are read by slicing the stored files, so the host does not need to support HTTP Range requests.

### Building and publishing the data

After building the dictionary with `fetch` and `build`, write the files for the browser (about 20 seconds, about 255 MB of output).

```powershell
.venv\Scripts\python -m badapple_unicode web-export --dictionary cache/full-dictionary --output web/data
```

To try it locally, serve `web/` with a static server.

```powershell
node tools/serve-web.mjs 8000
```

`web/data/` is build output, so it is not committed to the main branch. The site is published as a Cloudflare Worker with static assets only. Requests for static assets are free and unlimited. It is served only on the given domain; the `workers.dev` and preview URLs are disabled. The domain must be a zone in the same Cloudflare account. Log in once with `npx wrangler@4.151.2 login`. Glyph files are split into 20 MB pieces to stay under Cloudflare's 25 MiB per-file limit.

```powershell
.\tools\publish-cloudflare.ps1 -Domain unicode-frames.ikutotest.xyz
```

If Cloudflare Access protects every host by default, the published hostname needs an Access application with a "Bypass / Everyone" policy.

Browser support: WebCodecs is required (recent Chrome / Edge, Safari, Firefox). WebGPU makes matching much faster.

## Requirements

- Python 3.11 or later
- FFmpeg / FFprobe on PATH
- (Optional) NVIDIA GPU: PyTorch with CUDA, or the CUDA library for the grid versions (CUDA Toolkit + MSVC)
- About 1 GB of disk and a network connection to fetch the fonts, Unicode data and source video

## Running (Windows)

```powershell
.\setup.ps1
.\run.ps1
```

`run.ps1` fetches the fonts and source video (`assets/`), builds the dictionary (`cache/`), then matches and renders the video (`output/`). Later runs reuse the dictionary.

You can also give another video.

```powershell
.\run.ps1 -InputVideo 'D:\videos\badapple.mp4' -OutputDirectory output-custom
```

## Running (macOS / Linux / manual)

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[cpu,test]'
.venv/bin/python -m badapple_unicode fetch
.venv/bin/python -m badapple_unicode build --dictionary cache/full-dictionary
.venv/bin/python -m badapple_unicode run --dictionary cache/full-dictionary
.venv/bin/python -m badapple_unicode play output/characters.txt
```

The versions it was tested with are in `requirements-lock.txt`.

## Dictionary and fallback

- For every family in the [official Noto distribution repository](https://github.com/notofonts/notofonts.github.io), the Full Regular is fetched. Families without a Full build use the Unhinted build, and families without a Regular use an available instance. This covers Sans / Serif / Mono and historic scripts and symbols.
- [Noto CJK](https://github.com/notofonts/noto-cjk) Sans / Serif in all five regions and the monochrome [Noto Emoji](https://github.com/google/fonts/tree/main/ofl/notoemoji) are included.
- [GNU Unifont 17.0.05](https://ftp.gnu.org/gnu/unifont/unifont-17.0.05/) is added: the base plane, upper planes, Japanese and Traditional Chinese versions.
- Noto and Unifont leave large gaps in the CJK extensions, so the three [Jigmo 2025-09-12](https://kamichikoichi.github.io/jigmo/) fonts are added too. `fetch --no-jigmo` leaves them out.
- **To use every weight, width and italic as well, run `fetch --all-styles` or `.\run.ps1 -AllStyles`.** By default each family's standard style is used. Variable fonts are not enumerated across their continuous axes; their default instance is drawn.

The targets are Unicode 17's assigned scalar values, excluding control, format, private-use, surrogate, unassigned, whitespace and replacement characters. Combining characters are included when they draw ink on their own. Each font's cmap and glyph IDs are checked, and empty drawings and tofu identical to `.notdef` are removed. Real square symbols are not excluded just because of their shape.

By default every eligible character present in each font is a candidate. The same character from another font is a separate candidate if it looks different. `build --fallback-only` is a faster option that keeps only the first font that can draw each character.

Glyphs are drawn, cropped to their ink, on a shared 128×96 canvas. Candidates whose stretched and aspect-preserving canvases both match pixel for pixel are deduplicated by SHA-256. Candidates that only look alike after the 32×24 reduction and blur are not treated as duplicates. Which other names, code points and fonts map to each glyph is kept in the SQLite `aliases` table.

**The font set does not cover every character. So as not to claim full Unicode coverage, the counts are written to `manifest.json` and each missing character to `missing.tsv`.** Empty combining marks, variation selectors and the like also appear in this list. Adding fonts and building a separate dictionary fills the gaps.

```powershell
.venv\Scripts\python -m badapple_unicode build --fonts assets/fonts extra-fonts --dictionary cache/expanded
```

## Normalization and comparison

`stretch` stretches the character's ink to fill a 4:3 frame; `fit` keeps its aspect ratio and centers it. The source video is reduced to the same 4:3 canvas in both modes. Both are brought to 32×24 and given a Gaussian blur with a 0.65 px standard deviation. The foreground is always white and the background black. Per-frame inversion, mirroring, character sequences and composed characters are not used.

The default `--normalization auto` takes 128 evenly spaced frames from the whole video, searches every candidate with both modes and uses the mode with the smaller mean distance. `normalization-comparison.png` and `comparison.json` show the basis for the choice. `--normalization stretch` / `fit` fixes the mode.

`--metric l2` is the mean squared error over pixel values from 0 to 1. `--metric iou` is `1 - sum(min(A,B))/sum(max(A,B))` over the blurred masks; when both are empty the distance is defined as 0.

The exhaustive search is the matrix product `||q||² + ||g||² - 2 qgᵀ`, split into chunks to limit memory. There is no approximate search and no temporal smoothing: each frame's nearest neighbor is chosen independently.

| Backend | Notes |
| --- | --- |
| `auto` | Picks PyTorch with CUDA → FAISS (L2) → NumPy, in that order |
| `faiss` | Exhaustive search with CPU `IndexFlatL2`; no training needed |
| `numpy` | No extra search library; supports both L2 and IoU |
| `cuda` | Needs PyTorch with CUDA; float32 with TF32 off; supports both L2 and IoU |

FAISS is the optional dependency `.[cpu]` and PyTorch is `.[gpu]`. See the [PyTorch site](https://pytorch.org/get-started/locally/) for installing the CUDA build. IoU exhaustive search on the CPU is heavier than L2, so CUDA is recommended for large runs.

```powershell
.venv\Scripts\python -m badapple_unicode run --dictionary cache/full-dictionary --backend cuda
.venv\Scripts\python -m badapple_unicode run --dictionary cache/full-dictionary --metric iou --backend numpy
```

The source video's fractional frame rate (30000/1001 for this video) is kept. Variable-frame-rate sources are sampled to a constant rate at the same fps. `--fps 30` or `--max-frames 300` change this. The dictionary and frames are kept in reusable caches; a `.building` folder left by an interrupted run is never used.

## High-resolution rendering and re-rendering

The 128 px glyphs used for matching are separate from the glyphs drawn in the video. For the video, outlines are taken from the original TTF / OTF, the stretch is applied to the vector coordinates, and the glyph is drawn at twice the display size and scaled down. This avoids the blur of enlarging a small raster image, and thin horizontal strokes are not limited by the pixels of the unstretched glyph. The 32×24 matching features and the chosen characters are not recomputed.

This command reuses an existing `frames.jsonl` to make a 2560×1920 high-definition version, skipping dictionary building and the search.

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --encoder h264_nvenc
```

The output is `output/badapple-unicode-hq.mp4`, plus a `.render.json` recording the rendering settings. Without an NVIDIA encoder use `--encoder libx264`; for the original video size use `--size 1280x960`. New `run`s use the same outline rendering by default. `--supersample` accepts 1 to 4.

This command makes a version with only the character and the audio. The chosen character fills the 4:3 screen, with no original-video inset, title, captions or number lines. The character choices and frame rate are reused from the existing results.

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --layout glyph-only --encoder h264_nvenc
```

The output is `output/badapple-unicode-glyph-only.mp4` (2560×1920, with audio).

The original font and face index within a font collection are kept. A font whose SHA-256 differs from when the dictionary was built is rejected, so the typeface never changes unexpectedly. The high-resolution display cache is stored in `cache/full-dictionary/display-cache/`. Use `--glyph-renderer cached` only to reproduce the old method for comparison.

Unifont's stair-stepped outlines are the pixel shapes of the original font. Drawing the outlines directly does not turn them into smooth curves like other typefaces.

## Fixed captions and position in Unicode

The bottom captions use a monospaced font and show the code point, character name, font name, normalization mode and frame number at fixed positions. Lines share baselines, so a long font name never moves the fields after it. The frame number has a fixed number of digits.

Next to the code point are the Unicode block name and its range. The data is the official Unicode 17 `Blocks.txt`, bundled in the package, with its license in `badapple_unicode/data/UNICODE-LICENSE.txt`.

- The `UNICODE` number line covers every code point from U+0000 to U+10FFFF. The character's block is shown in teal and the chosen character's position in yellow. The short ticks mark the start of each Unicode plane.
- The `BLOCK` number line zooms in from the start to the end of the current block, so positions can be read even for characters that crowd the left end of the whole Unicode range.

Both use positions proportional to the code point value. The ticks and endpoint labels stay fixed; only the marker moves as the character changes. The layout keeps the central character large while fitting both number lines at the bottom.

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --output output/badapple-unicode-hq-position.mp4 --encoder h264_nvenc
```

## Output

| File | Contents |
| --- | --- |
| `badapple-unicode.mp4` | 1280×960: character + original video + character name + audio |
| `badapple-unicode.poster.png` | A still from the video |
| `badapple-unicode-hq.mp4` | 2560×1920 outline-rendered version reusing the original choices |
| `badapple-unicode-hq-position.mp4` | High-definition version with fixed captions, Unicode block name and two number lines |
| `badapple-unicode-glyph-only.mp4` | Only the full-screen character and the audio |
| `characters.txt` | UTF-8, no BOM or newlines, one scalar value per frame |
| `characters-collapsed.txt` | Single-line UTF-8 text with runs of the same character merged into one |
| `timing.json` | Fractional fps and frame count |
| `characters.zip` | The text, timing and a playback script that needs no extra libraries |
| `frames.jsonl` | Each frame's character, name, font, distance and time |
| `characters.srt` | Subtitles that merge runs of the same choice |
| `normalization-comparison.png` | Original frames next to the stretch and fit results |
| `comparison.json` / `summary.json` | Reports on the normalization choice, search results and timings |

With `characters.txt` and `timing.json` in the same folder, `play` plays them back. Unicode normalization can change the number of code points, so keep the text as-is. How it looks in a terminal depends on the terminal's font fallback and cell size, and some terminals cannot show combining characters or characters from the upper planes. The video is drawn from the chosen original fonts, so it does not depend on the fonts where it is played.

The distributable `characters.zip` plays with just `python play_characters.py` after extracting; it needs nothing but Python.

`characters-collapsed.txt` is also in the ZIP. For example, `AAABBAA` becomes `ABA`: separate reappearances are kept, and no Unicode normalization is applied. Its length differs from the frame count, so the original fps does not apply to it.

Characters that amount to a blank page are excluded from the candidates, so even a completely black frame gets some character.

## Reference: results on the author's machine

244 fonts, 155,230 code points, 410,510 visually distinct candidates. All 6,955 frames were searched exhaustively with FAISS on the CPU, and a 1280×960 video with audio was written with H.264 NVENC. Matching and rendering took about 122 seconds in total; building the dictionary took about 440 seconds. 902 distinct characters were chosen.

The mean MSE over 128 samples was 0.061099 for stretch and 0.083048 for fit, about 26% lower for stretch.

4,381 characters are missing or draw no ink on their own: 3,995 Egyptian hieroglyphs, 240 variation selectors, 115 Tangut components, 30 Tangut ideographs and 1 empty Hangul filler.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

Small synthetic fonts check the exclusion of characters missing from the cmap, tofu and blanks, the upper planes, visual duplicates and fallback to later fonts. The search is checked against brute-force distances. Where FFmpeg is available, the tests also check the fractional frame rate, every frame, the audio, and that the text and metadata agree. Outline rendering, the fixed caption layout and the grid layouts are tested too.

## License

The code in this repository is under the [MIT License](LICENSE). The fonts, video and Unicode data below are under their own licenses.

## Assets and licenses

This repository does not include the fonts or the source video. `fetch` downloads them when run and records the source URLs, Git revisions and SHA-256 hashes in `assets/manifest.json`, and the video's source in `assets/source.json`. Each font's license text is saved in `assets/licenses/`.

- Source video: the public copy in [bad-apple-lab/Bad-Apple](https://github.com/bad-apple-lab/Bad-Apple). Rights to the original video and music belong to their respective owners.
- Fonts: Noto (SIL OFL 1.1), GNU Unifont (GPL with font embedding exception / OFL), Jigmo (CC0 1.0)
- Unicode data: `badapple_unicode/data/Blocks-17.0.0.txt` is under Unicode, Inc.'s [Unicode License v3](badapple_unicode/data/UNICODE-LICENSE.txt).
