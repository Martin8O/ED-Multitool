"""The release zip players install: one `EDMultitool/` folder that EDMC can load, nothing private in it."""

import importlib
import re
import sys
import tomllib
import zipfile
from pathlib import Path

from edmultitool import PLUGIN_NAME, __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
make_release = importlib.import_module("make_release")  # a tool, not a package: loaded by path


def test_the_zip_holds_the_plugin_folder_and_nothing_private(tmp_path: Path) -> None:
    plugin = tmp_path / "EDMultitool"
    for rel in (
        "load.py",
        "edmultitool/__init__.py",
        "edmultitool/web/index.html",
        "edmultitool/web/app.js",
        "edmultitool/web/style.css",
        "edmultitool/web/emblem.png",
        "edmultitool/web/header-bg.jpg",
        "data/settings.json",
        "data/cache/x.py",
        "edmultitool/__pycache__/plugin.cpython-313.pyc",
        "edmultitool/notes.txt",
    ):
        (plugin / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugin / rel).write_text("x", encoding="utf-8")

    licence = tmp_path / "LICENSE"
    licence.write_text("MIT", encoding="utf-8")

    out = make_release.build(plugin, tmp_path / "dist", "9.9.9", licence)

    assert out.name == "ED-Multitool-9.9.9.zip"
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == [
            "EDMultitool/LICENSE",
            "EDMultitool/edmultitool/__init__.py",
            "EDMultitool/edmultitool/web/app.js",
            "EDMultitool/edmultitool/web/emblem.png",
            "EDMultitool/edmultitool/web/header-bg.jpg",
            "EDMultitool/edmultitool/web/index.html",
            "EDMultitool/edmultitool/web/style.css",
            "EDMultitool/load.py",
        ]


def test_the_real_plugin_ships_its_entry_point_page_and_catalogs() -> None:
    files = {p.as_posix() for p in make_release.release_files()}
    assert {
        "load.py",
        "edmultitool/web/index.html",
        "edmultitool/web/app.js",
        "edmultitool/web/style.css",
    } <= files
    assert any(f.startswith("edmultitool/catalog/") for f in files)
    assert not any(f.startswith("data/") or "__pycache__" in f for f in files)


def test_every_file_the_page_loads_ships() -> None:
    """A picture left out of the zip is a page without its emblem, header or icon on every player's PC."""
    web = ROOT / "EDMultitool" / "edmultitool" / "web"
    files = {p.as_posix() for p in make_release.release_files()}
    wanted: set[str] = set()
    for page in ("index.html", "style.css"):
        text = (web / page).read_text(encoding="utf-8")
        refs = re.findall(r"""(?:src|href)="([^"#:?]+)"|url\(["']?([^"')#:?]+)""", text)
        wanted |= {a or b for a, b in refs}
    wanted = {w.lstrip("./") for w in wanted if not w.startswith(("/", "data:"))}
    assert {"emblem.png", "header-bg.jpg", "favicon.png"} <= wanted  # the matcher sees the real pictures
    assert {f"edmultitool/web/{w}" for w in wanted} <= files


def test_the_licence_ships() -> None:
    assert make_release.LICENCE.is_file()
    assert "MIT License" in make_release.LICENCE.read_text(encoding="utf-8")


def test_one_version_everywhere() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__
    assert PLUGIN_NAME == "ED Multitool"
