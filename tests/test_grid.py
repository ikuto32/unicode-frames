import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from badapple_unicode.cuda_match import exact_u8_search, native_available
from badapple_unicode.glyphs import Dictionary, GlyphConfig, build_dictionary
from badapple_unicode.grid import GridLegend, match_cells, run_grid, tile_features
from badapple_unicode.outline import OutlineRenderer
from badapple_unicode.video import probe


@pytest.mark.parametrize("columns,rows",[(4,3),(8,6)])
def test_grid_splits_cells_in_row_major_order(columns,rows):
    count = columns*rows
    values = np.arange(count,dtype=np.uint8).reshape(rows,columns)*5
    image = Image.fromarray(np.repeat(np.repeat(values,4,axis=0),4,axis=1))
    config = GlyphConfig(feature_width=2,feature_height=2,blur=0)
    vectors = tile_features(image,columns,rows,config)
    assert vectors.shape == (count,4)
    np.testing.assert_array_equal(vectors,np.repeat(values.reshape(-1,1),4,axis=1))


def test_matching_restores_duplicate_cells_and_frame_order():
    bank = np.array([[0,0,0,0],[255,255,255,255]],np.uint8)
    layout = np.array([[0,1,0,1],[1,0,1,0]])
    queries = bank[layout]
    ids,scores,backend,distinct = match_cells(queries,bank,backend="numpy")
    np.testing.assert_array_equal(ids,layout)
    np.testing.assert_array_equal(scores,np.zeros_like(scores))
    assert distinct == 2 and backend == "numpy"


@pytest.mark.skipif(not native_available(),reason="Optional native CUDA matcher not built")
@pytest.mark.parametrize("dimension",[24,768])
def test_native_integer_search_is_exact_with_partial_batches(dimension):
    rng = np.random.default_rng(381)
    queries = rng.integers(0,256,(11,dimension),dtype=np.uint8)
    bank = rng.integers(0,256,(37,dimension),dtype=np.uint8)
    bank[-1] = queries[-1]
    ids,scores,backend = exact_u8_search(queries,bank,query_batch=8,candidate_batch=16)
    reference = ((queries[:,None,:].astype(np.int32)-bank[None,:,:].astype(np.int32))**2).sum(2)
    np.testing.assert_array_equal(ids,reference.argmin(1))
    np.testing.assert_allclose(scores,reference.min(1)/(65025*dimension),atol=1e-8)
    assert backend == "native-int8"


@pytest.mark.skipif(not native_available(),reason="Optional native CUDA matcher not built")
def test_native_extremes_and_equal_glyphs_choose_first_candidate():
    queries = np.array([[0]*768,[255]*768],np.uint8)
    bank = np.array([[255]*768,[0]*768,[0]*768],np.uint8)
    ids,scores,_ = exact_u8_search(queries,bank,query_batch=4,candidate_batch=4)
    np.testing.assert_array_equal(ids,[1,0])
    np.testing.assert_array_equal(scores,[0,0])


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="FFmpeg unavailable")
@pytest.mark.parametrize("columns,rows",[(4,3),(8,6)])
def test_complete_grid_video_audio_and_fixed_character_count(synthetic_fonts,tmp_path,columns,rows):
    source = tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i","testsrc2=size=96x72:rate=30000/1001",
        "-f","lavfi","-i","sine=frequency=440:duration=1","-frames:v","12","-c:v","libx264",
        "-pix_fmt","yuv420p","-c:a","aac",str(source)],check=True)
    dictionary = tmp_path/"dictionary"
    build_dictionary([synthetic_fonts],dictionary,GlyphConfig(),codepoints={65,66,69,0x1F34E})
    output = tmp_path/"output"
    summary = run_grid(source,dictionary,output,tmp_path/"frames",backend="numpy",size=(640,480),columns=columns,rows=rows)
    count = columns*rows
    assert summary["frames"] == 12 and summary["characters_per_frame"] == count
    assert summary["cell_queries"] == 12*count
    assert summary["whitespace_candidates"] is False
    assert summary["code_points_shown"] is True and summary["glyph_legend_shown"] is True
    assert summary["feature_source_size"] == [max(320,80*columns),max(240,80*rows)]
    text = (output/"characters.txt").read_text(encoding="utf-8").splitlines()
    assert len(text)==12 and all(len(line)==count for line in text)
    assert all(not char.isspace() for line in text for char in line)
    records = [json.loads(line) for line in (output/"frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all([ord(c) for c in line]==record["codepoints"] for line,record in zip(text,records))
    assert all(len(record["glyph_ids"])==len(record["distances"])==count for record in records)
    video = output/f"badapple-unicode-{columns}x{rows}.mp4"
    info = probe(video)
    assert info["has_audio"] and info["fps"] == "30000/1001"
    assert info["duration"] == pytest.approx(12*1001/30000,abs=0.001)
    raw = json.loads(subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","stream=nb_frames","-of","json",str(video)]))
    assert int(raw["streams"][0]["nb_frames"]) == 12
    # The lossless poster must contain every selected font's own glyph, rather
    # than relying on the UI font's Unicode coverage or text shaping.
    legend = GridLegend((640,480),count)
    with Image.open(video.with_suffix(".poster.png")) as poster:
        dictionary = Dictionary(dictionary)
        outline = OutlineRenderer(dictionary)
        try:
            for at,glyph_id in enumerate(records[-1]["glyph_ids"]):
                x,y = legend.positions[at]
                x += round(17*legend.sx)
                w,h = legend.glyph_size
                assert 0<=x<x+w<=poster.width and 0<=y<y+h<=poster.height
                actual = poster.crop((x,y,x+w,y+h)).convert("L")
                expected = outline.render(glyph_id,legend.glyph_size,"fit")
                np.testing.assert_array_equal(actual,expected)
        finally:
            outline.close()
            dictionary.close()
