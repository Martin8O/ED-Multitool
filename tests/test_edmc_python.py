"""EDMC runs the plugin on its own frozen Python, which bundles only the standard modules EDMC itself uses
(6.1.2 has no `difflib`: the gear lookup once failed to load there while every test here passed). Every
module the plugin imports must be one EDMC is known to bundle; a new one is checked in EDMC's library.zip
first, then added."""

import ast
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "EDMultitool"

# Checked against EDMC 6.1.2's library.zip + its .pyd files + builtins (2026-10-12).
EDMC_BUNDLES = frozenset(
    {
        "collections", "contextlib", "dataclasses", "datetime", "email", "hashlib", "http", "itertools",
        "json", "logging", "math", "os", "pathlib", "queue", "re", "shlex", "socket", "socketserver",
        "sqlite3", "subprocess", "sys", "threading", "time", "tkinter", "typing", "unicodedata", "urllib",
        "webbrowser", "winreg",
    }
)  # fmt: skip
# The plugin's own package and the modules EDMC itself provides to plugins.
OURS_AND_EDMC = frozenset(
    {"edmultitool", "myNotebook", "config", "theme", "plug", "l10n", "monitor", "requests"}
)


def _imports() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for f in PLUGIN.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                used.setdefault(name.split(".")[0], set()).add(f.relative_to(PLUGIN).as_posix())
    return used


def test_every_import_is_bundled_with_edmc() -> None:
    unknown = {m: sorted(files) for m, files in _imports().items() if m not in EDMC_BUNDLES | OURS_AND_EDMC}
    assert unknown == {}, "not known to be in EDMC's Python: check its library.zip, then add it above"
