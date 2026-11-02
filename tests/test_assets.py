import pytest

from badapple_unicode import assets


def test_font_download_rejects_html_even_when_http_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "read_url", lambda url: b"<!DOCTYPE html><html>verification required</html>")
    target = tmp_path / "font.otf"
    with pytest.raises(RuntimeError, match="Invalid download"):
        assets.download("https://example.com/font.otf", target)
    assert not target.exists()


def test_corrupted_cached_font_is_downloaded_again(tmp_path, monkeypatch):
    target = tmp_path / "font.otf"
    target.write_bytes(b"<html>not a font</html>")
    monkeypatch.setattr(assets, "read_url", lambda url: b"OTTOtest-font-bytes")
    result = assets.download("https://example.com/font.otf", target)
    assert target.read_bytes().startswith(b"OTTO")
    assert result["bytes"] == len(target.read_bytes())


def test_download_verifies_upstream_git_blob_id(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "read_url", lambda url: b"OTTOtest-font-bytes")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        assets.download("https://example.com/font.otf", tmp_path / "font.otf", "0"*40)
