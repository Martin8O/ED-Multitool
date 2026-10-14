"""Build the release zip players install: `dist/ED-Multitool-<version>.zip`.

The zip holds one folder, `EDMultitool/`, exactly as EDMC loads it: `load.py` plus the `edmultitool` package
(code, the page with its pictures, bundled catalogs) and the licence. Never in it: runtime data (`data/`),
compiled caches, tests, tools, docs.
A player unzips it into EDMC's plugins folder and restarts EDMC (README, "Install").

Usage (from repo root, after `. tools/env.ps1`):
    .venv/Scripts/python tools/make_release.py
"""

import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "EDMultitool"
DIST = ROOT / "dist"
LICENCE = ROOT / "LICENSE"

sys.path.insert(0, str(PLUGIN))

from edmultitool import __version__  # noqa: E402

# Only these file types ship; anything else in the plugin folder is a leftover and stays out.
SHIPPED_SUFFIXES = frozenset({".py", ".html", ".js", ".css", ".png", ".jpg", ".svg", ".webp", ".ico"})
SKIPPED_DIRS = frozenset({"data", "__pycache__"})


def release_files(plugin: Path = PLUGIN) -> Iterator[Path]:
    """Every file that ships, relative to the plugin folder, in a stable order."""
    for f in sorted(plugin.rglob("*")):
        rel = f.relative_to(plugin)
        if f.is_file() and f.suffix in SHIPPED_SUFFIXES and not SKIPPED_DIRS & set(rel.parts[:-1]):
            yield rel


def build(
    plugin: Path = PLUGIN, dist: Path = DIST, version: str = __version__, licence: Path = LICENCE
) -> Path:
    """Write the zip (replacing an older one of the same version) and return its path."""
    dist.mkdir(parents=True, exist_ok=True)
    out = dist / f"ED-Multitool-{version}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in release_files(plugin):
            zf.write(plugin / rel, (Path(plugin.name) / rel).as_posix())
        zf.write(licence, f"{plugin.name}/LICENSE")
    return out


if __name__ == "__main__":
    path = build()
    with zipfile.ZipFile(path) as zf:
        count = len(zf.namelist())
    print(f"{path.relative_to(ROOT)}: {count} files, {path.stat().st_size // 1024} KiB")
