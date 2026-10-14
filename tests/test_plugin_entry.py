import importlib.util
from pathlib import Path

import edmultitool

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "EDMultitool"


def test_version_is_semver() -> None:
    parts = edmultitool.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_plugin_start3_returns_display_name() -> None:
    # Load load.py the way EDMC does: from its file path, not as a package module.
    spec = importlib.util.spec_from_file_location("edmultitool_load", PLUGIN_DIR / "load.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.plugin_start3(str(PLUGIN_DIR)) == "ED Multitool"
    module.plugin_stop()
