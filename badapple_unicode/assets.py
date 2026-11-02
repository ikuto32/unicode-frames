"""Download official fonts, recording immutable revisions and file checksums."""
from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

UNIFONT_VERSION = "17.0.05"
SOURCE_REPO = "bad-apple-lab/Bad-Apple"


def read_url(url: str) -> bytes:
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "badapple-unicode/0.1"}), timeout=120) as response:
                return response.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(url)


def github_tree(repo: str, ref: str = "main") -> tuple[str, list[dict]]:
    result = json.loads(read_url(f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"))
    if result.get("truncated"):
        raise RuntimeError(f"Truncated GitHub tree: {repo}; refusing an incomplete font collection")
    return result["sha"], result["tree"]


def raw_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(path, safe='/')}"


def download(url: str, path: Path, expected_git_sha: str | None = None) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    def validate(data):
        if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".otc"}:
            return data[:4] in {b"OTTO", b"\0\1\0\0", b"ttcf", b"true"}
        if path.name.endswith(".tar.gz"):
            return data[:2] == b"\x1f\x8b"
        if path.suffix.lower() == ".zip":
            return data[:4] == b"PK\x03\x04"
        return not data.lstrip().lower().startswith((b"<!doctype html", b"<html"))
    data = path.read_bytes() if path.exists() else b""
    if not data or not validate(data):
        data = read_url(url)
    if not validate(data):
        raise RuntimeError(f"Invalid download content (possibly an HTML challenge): {url}")
    if expected_git_sha:
        digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if digest != expected_git_sha:
            # A previously cached file can belong to an older upstream revision.
            data = read_url(url)
            digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if digest != expected_git_sha:
                raise RuntimeError(f"Git blob checksum mismatch: {path}")
    if not path.exists() or path.read_bytes() != data:
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_bytes(data)
        partial.replace(path)
    return {"path": str(path.resolve()), "url": url, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def fetch_assets(root: Path, all_styles: bool = False, workers: int = 8,
                 include_video: bool = True, include_jigmo: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, Path, str | None]] = []
    revisions = {}
    repo = "notofonts/notofonts.github.io"
    sha, tree = github_tree(repo)
    revisions[repo] = sha
    families: dict[str, dict[str, list[dict]]] = {}
    for item in tree:
        parts = item["path"].split("/")
        if (len(parts) == 5 and parts[0] == "fonts" and parts[2] in {"full", "unhinted"} and parts[3] == "ttf"
                and parts[4].endswith(".ttf")):
            families.setdefault(parts[1], {}).setdefault(parts[2], []).append(item)
    if not families:
        raise RuntimeError("No Noto families found; upstream layout may have changed")
    for family, formats in sorted(families.items()):
        # Full builds can have encoded characters absent from stripped builds.
        items = formats.get("full", formats.get("unhinted", []))
        # All script families, sans/serif/mono etc. Static Regular is the default
        # instance; --all-styles additionally includes every weight and width.
        regular = [item for item in items if item["path"].endswith("-Regular.ttf")]
        selected = items if all_styles else (regular or sorted(items, key=lambda i: i["path"])[:1])
        for item in selected:
            jobs.append((raw_url(repo, sha, item["path"]),
                         root / "fonts" / "noto" / family / Path(item["path"]).name, item["sha"]))
    jobs.append((raw_url(repo, sha, "fonts/LICENSE"), root / "licenses" / "Noto-OFL.txt", None))

    repo = "notofonts/noto-cjk"
    sha, tree = github_tree(repo)
    revisions[repo] = sha
    for item in tree:
        p = item["path"]
        parts = p.split("/")
        if (len(parts) == 4 and parts[0] in {"Sans", "Serif"} and parts[1] == "OTF"
                and p.endswith(".otf") and (all_styles or p.endswith("-Regular.otf"))):
            jobs.append((raw_url(repo, sha, p), root / "fonts" / "cjk" / parts[0] / Path(p).name, item["sha"]))
        if p in {"Sans/LICENSE", "Serif/LICENSE"}:
            jobs.append((raw_url(repo, sha, p), root / "licenses" / (parts[0] + "-OFL.txt"), item["sha"]))

    repo = "google/fonts"
    # The monochrome emoji font avoids platform-dependent color-font rendering.
    info = json.loads(read_url(f"https://api.github.com/repos/{repo}/commits/main"))
    sha = info["sha"]
    revisions[repo] = sha
    for filename in ["NotoEmoji[wght].ttf", "OFL.txt"]:
        target = root / ("fonts/emoji" if filename.endswith(".ttf") else "licenses/emoji") / filename
        jobs.append((raw_url(repo, sha, "ofl/notoemoji/" + filename), target, None))

    # GNU's distribution mirror serves actual binaries without an HTML challenge.
    base = f"https://ftp.gnu.org/gnu/unifont/unifont-{UNIFONT_VERSION}/"
    for stem in ["unifont", "unifont_upper", "unifont_jp", "unifont_t"]:
        filename = f"{stem}-{UNIFONT_VERSION}.otf"
        jobs.append((base + filename, root / "fonts" / "unifont" / filename, None))
    jobs.append((base + f"unifont-{UNIFONT_VERSION}.tar.gz",
                 root / "licenses" / f"unifont-{UNIFONT_VERSION}.tar.gz", None))
    if include_jigmo:
        jobs.append(("https://kamichikoichi.github.io/jigmo/Jigmo-20250912.zip",
                     root / "downloads" / "Jigmo-20250912.zip", None))
    manifest = {"noto_style_policy": "all-styles" if all_styles else "regular-per-family",
                "revisions": revisions, "unifont_version": UNIFONT_VERSION, "files": []}
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download, *job): job for job in jobs}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                manifest["files"].append(future.result())
            except Exception as exc:
                failures.append({"url": futures[future][0], "error": str(exc)})
            if i % 20 == 0 or i == len(jobs):
                print(f"assets: {i}/{len(jobs)}", flush=True)
    manifest["files"].sort(key=lambda item: item["path"])
    manifest["failures"] = failures
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError(f"{len(failures)} downloads failed; see {root / 'manifest.json'} and rerun fetch")
    # Extract only known license texts, without trusting tar member paths.
    with tarfile.open(root / "licenses" / f"unifont-{UNIFONT_VERSION}.tar.gz", "r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile() and Path(member.name).name in {"COPYING", "COPYING.LESSER", "COPYING.LIB"}:
                content = archive.extractfile(member).read()
                (root / "licenses" / ("Unifont-" + Path(member.name).name + ".txt")).write_bytes(content)
    if include_jigmo:
        with zipfile.ZipFile(root / "downloads" / "Jigmo-20250912.zip") as archive:
            for name in archive.namelist():
                filename = Path(name).name
                if filename.lower().endswith(".ttf"):
                    path = root / "fonts" / "jigmo" / filename
                elif filename.lower().endswith(".txt"):
                    path = root / "licenses" / ("Jigmo-" + filename)
                else:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(name)
                if path.suffix.lower() == ".ttf" and data[:4] != b"\0\1\0\0":
                    raise RuntimeError(f"Invalid Jigmo font: {filename}")
                path.write_bytes(data)
                manifest["files"].append({"path": str(path.resolve()), "url": jobs[-1][0] + "#" + name,
                                          "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if include_video:
        sha = json.loads(read_url(f"https://api.github.com/repos/{SOURCE_REPO}/commits/main"))["sha"]
        record = download(raw_url(SOURCE_REPO, sha, "badapple.mp4"), root / "badapple.mp4")
        record.update(repository=f"https://github.com/{SOURCE_REPO}", commit=sha)
        (root / "source.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Fetched {len(manifest['files'])} files; all download checks passed.", flush=True)
