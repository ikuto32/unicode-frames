import json
import shutil
import subprocess
import zipfile
from fractions import Fraction

import pytest
import numpy as np
from PIL import Image

from badapple_unicode.glyphs import Dictionary, GlyphConfig, build_dictionary
from badapple_unicode.outline import OutlineRenderer
from badapple_unicode.pipeline import collapsed_characters, render_existing, run_pipeline
from badapple_unicode.video import probe, read_exact


def test_truncated_frame_is_an_error():
    import io
    with pytest.raises(RuntimeError, match="Truncated"):
        read_exact(io.BytesIO(b"abc"), 4)


def test_collapse_adjacent_characters_preserves_order_and_later_repeats():
    assert collapsed_characters("AAABBAA") == "ABA"
    assert collapsed_characters("𓍕𓍕🙏🙏a\u0301\u0301a") == "𓍕🙏a\u0301a"
    assert collapsed_characters("") == ""


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_complete_fractional_fps_video_audio_and_unicode_export(synthetic_fonts, tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x72:rate=30000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
                    "-frames:v", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    dictionary = tmp_path / "dictionary"
    build_dictionary([synthetic_fonts], dictionary, GlyphConfig(), codepoints={65,66,69,0x1F34E})
    output = tmp_path / "output"
    result = run_pipeline(source, dictionary, output, tmp_path / "frames", backend="numpy",
                           sample_count=6, size=(640,480))
    assert result["frames"] == 12
    text = (output / "characters.txt").read_text(encoding="utf-8")
    assert len(text) == 12
    assert (output / "characters-collapsed.txt").read_text(encoding="utf-8") == collapsed_characters(text)
    records = [json.loads(line) for line in (output / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(ord(c) == r["codepoint"] for c,r in zip(text,records))
    assert records[1]["time"] == pytest.approx(1001/30000)
    timing = json.loads((output / "timing.json").read_text())
    assert Fraction(timing["fps"]) == Fraction(30000,1001)
    info = probe(output / "badapple-unicode.mp4")
    assert info["has_audio"]
    assert Fraction(info["fps"]) == Fraction(30000,1001)
    assert info["duration"] == pytest.approx(12*1001/30000, abs=0.001)
    frames_info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames", "-of", "json", str(output / "badapple-unicode.mp4")]))
    assert int(frames_info["streams"][0]["nb_frames"]) == 12
    assert (output / "normalization-comparison.png").exists()
    assert (output / "characters.srt").read_text().startswith("1\n00:00:00,000 --> ")
    with zipfile.ZipFile(output / "characters.zip") as archive:
        assert archive.read("characters.txt").decode("utf-8") == text
        assert {"characters.txt", "characters-collapsed.txt", "timing.json", "play_characters.py", "README.txt"} == set(archive.namelist())
        assert archive.read("characters-collapsed.txt").decode("utf-8") == collapsed_characters(text)
    # Standalone playback remains usable without the project's third-party imports.
    subprocess.run([__import__('sys').executable, "-S", str(output / "play_characters.py"), "--speed", "1000"],
                   check=True, capture_output=True)
    recorded_bytes = (output / "frames.jsonl").read_bytes()
    rerender = render_existing(output, output / "high-quality.mp4", tmp_path / "frames", size=(640,480))
    assert rerender["frames"] == 12
    assert rerender["glyph_renderer"] == "outline"
    assert rerender["supersample"] == 2
    assert rerender["caption_layout"] == "fixed-columns-unicode-lines-v1"
    assert rerender["unicode_blocks_version"] == "17.0.0"
    assert (output / "frames.jsonl").read_bytes() == recorded_bytes
    high_quality = probe(output / "high-quality.mp4")
    assert high_quality["has_audio"]
    assert high_quality["fps"] == info["fps"]
    assert high_quality["duration"] == pytest.approx(info["duration"], abs=0.001)
    # The clean layout must never load source thumbnails or create any caption.
    def forbidden(*args,**kwargs):
        raise AssertionError("Glyph-only output must contain only the selected glyph")
    monkeypatch.setattr("badapple_unicode.video.Frames.thumbnails",forbidden)
    monkeypatch.setattr("badapple_unicode.video.CaptionFooter",forbidden)
    monkeypatch.setattr("badapple_unicode.video.ui_font",forbidden)
    clean = render_existing(output,None,tmp_path/"frames",size=(640,480),layout="glyph-only")
    assert clean["layout"] == "glyph-only" and clean["original_preview"] is False
    assert clean["caption_layout"] is None and clean["number_line_domains"] == []
    clean_path = output/"badapple-unicode-glyph-only.mp4"
    clean_info = probe(clean_path)
    assert clean_info["has_audio"] and clean_info["fps"] == info["fps"]
    assert clean_info["duration"] == pytest.approx(info["duration"],abs=0.001)
    with Image.open(clean_path.with_suffix(".poster.png")) as poster:
        handle = Dictionary(dictionary)
        renderer = OutlineRenderer(handle)
        try:
            expected = renderer.render(records[-1]["glyph_id"],(640,480),timing["normalization"]).convert("RGB")
            np.testing.assert_array_equal(poster,expected)
        finally:
            renderer.close()
            handle.close()
    assert (output/"frames.jsonl").read_bytes() == recorded_bytes
