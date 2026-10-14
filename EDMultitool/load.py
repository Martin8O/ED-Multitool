"""EDMC entry point for ED Multitool. Thin adapter: EDMC hooks → `edmultitool.plugin.ScoutPlugin` → tk panel.

Every hook runs on EDMC's tk main thread. The other threads, all owned by `ScoutPlugin`, are the
localhost page server (its loop + one short-lived daemon thread per request) and one worker for online
lookups: started in plugin_start3, stopped in plugin_stop. None of them touches tk — finished worker
jobs are collected by a tk `after` timer on the main thread (`_pump`), cancelled in plugin_stop.
The EDMC-only `myNotebook` module is imported inside the hook that needs it, so this file also loads
outside EDMC (tests). EDMC themes the widgets created in plugin_app itself.
"""

import contextlib
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import ttk
from typing import Any, ClassVar

from edmultitool import PLUGIN_NAME, __version__
from edmultitool.browser import open_page
from edmultitool.core.settings import MAX_MARGIN_PCT, ROUTE_BASES, ROUTE_BASIS_LABELS, settings_from_form
from edmultitool.plugin import ScoutPlugin, plugin_logger

# EDMC puts both its plugins dir and this plugin's dir on sys.path, so `edmultitool` imports directly.
logger = plugin_logger(Path(__file__).resolve().parent)

PUMP_MS = 250  # how often the main thread collects finished background jobs
_ROWS = (
    ("scout", "System:"),
    ("ship", "Ship:"),
    ("range", "Range:"),
    ("routing", "Routing:"),
    ("next", "Next:"),
    ("here", "Here:"),
    ("earned", "Earned:"),
    ("missions", "Missions:"),
)


class _Ui:
    """Module-level holder for the plugin and its widgets (EDMC calls plain functions)."""

    plugin: ClassVar[ScoutPlugin | None] = None
    values: ClassVar[dict[str, tk.Label]] = {}
    basis_var: ClassVar[tk.StringVar | None] = None
    margin_var: ClassVar[tk.StringVar | None] = None
    frame: ClassVar[tk.Frame | None] = None
    copy_button: ClassVar[ttk.Button | None] = None
    note: ClassVar[tk.Label | None] = None
    warning: ClassVar[tk.Label | None] = None
    update: ClassVar[tk.Label | None] = None
    check_var: ClassVar[tk.BooleanVar | None] = None
    pump_after: ClassVar[str | None] = None


def plugin_start3(plugin_dir: str) -> str:
    """Called once by EDMC at startup; returns the display name."""
    _Ui.plugin = ScoutPlugin(Path(plugin_dir), journal_dir=_journal_dir())
    logger.info("%s %s loaded from %s", PLUGIN_NAME, __version__, plugin_dir)
    _Ui.plugin.start_server()
    _Ui.plugin.start_worker()
    return PLUGIN_NAME


def _journal_dir() -> Path | None:
    """The journal folder EDMC reads: its setting, else the game's default. Read once, only to learn the
    fleet from history; None outside EDMC or when neither is known."""
    try:
        from config import config  # EDMC-only module

        chosen = config.get_str("journaldir") or config.default_journal_dir
    except (ImportError, AttributeError):
        return None
    return Path(chosen) if chosen and str(chosen) != "None" else None


def plugin_stop() -> None:
    """Called by EDMC on shutdown: stop the job timer, the worker and the page server."""
    logger.info("%s stopping", PLUGIN_NAME)
    if _Ui.frame is not None and _Ui.pump_after is not None:
        with contextlib.suppress(tk.TclError):  # the widget is already gone
            _Ui.frame.after_cancel(_Ui.pump_after)
    _Ui.pump_after = None
    _Ui.frame = None
    _Ui.copy_button = None
    _Ui.note = None
    _Ui.warning = None
    _Ui.update = None
    if _Ui.plugin is not None:
        _Ui.plugin.stop_worker()
        _Ui.plugin.stop_server()
        _Ui.plugin.close()


def plugin_app(parent: tk.Frame) -> tk.Frame:
    """Rows in EDMC's main window: label in column 0, value in column 1 (EDMC applies its theme)."""
    frame = tk.Frame(parent)
    frame.columnconfigure(1, weight=1)
    _Ui.values = {}
    for row, (key, label) in enumerate(_ROWS):
        tk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W)
        value = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT)
        value.grid(row=row, column=1, sticky=tk.EW)
        _Ui.values[key] = value
    buttons = tk.Frame(frame)  # one row of buttons, side by side (column 0 is only as wide as a label)
    buttons.grid(row=len(_ROWS), column=0, columnspan=2, sticky=tk.W, pady=(2, 0))
    button = ttk.Button(buttons, text="Open ED Multitool", command=_open_page)
    button.pack(side=tk.LEFT)
    if _Ui.plugin is None or _Ui.plugin.server_url is None:
        button.state(["disabled"])
    _Ui.copy_button = ttk.Button(buttons, text="Copy next", command=_copy_next)
    _Ui.copy_button.pack(side=tk.LEFT, padx=(4, 0))
    # the panel's own line (page not running, on another port, no browser): shown only when there is one
    _Ui.note = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT, wraplength=300)
    _Ui.note.grid(row=len(_ROWS) + 1, column=0, columnspan=2, sticky=tk.EW)
    # a minor faction where you are holds you hostile: shown only then
    _Ui.warning = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT, wraplength=300)
    _Ui.warning.grid(row=len(_ROWS) + 2, column=0, columnspan=2, sticky=tk.EW)
    # "Version X is out": one clickable line, shown only when a newer version exists
    _Ui.update = tk.Label(frame, text="", anchor=tk.W, justify=tk.LEFT, wraplength=300, cursor="hand2")
    _Ui.update.grid(row=len(_ROWS) + 3, column=0, columnspan=2, sticky=tk.EW)
    _Ui.update.bind("<Button-1>", lambda _event: _open_update())
    _redraw()
    _Ui.frame = frame
    _Ui.pump_after = frame.after(PUMP_MS, _pump)
    return frame


def plugin_prefs(parent: Any, cmdr: str, is_beta: bool) -> Any:
    """'ED Multitool' tab in EDMC's settings."""
    import myNotebook as nb  # EDMC-only module

    plugin = _require_plugin()
    frame = nb.Frame(parent)
    frame.columnconfigure(1, weight=1)
    labels = [ROUTE_BASIS_LABELS[b] for b in ROUTE_BASES]
    _Ui.basis_var = tk.StringVar(value=ROUTE_BASIS_LABELS[plugin.settings.route_basis])
    _Ui.margin_var = tk.StringVar(value=f"{plugin.settings.margin_pct:g}")

    nb.Label(frame, text=f"{PLUGIN_NAME} {__version__}").grid(
        row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(6, 2)
    )
    nb.Label(frame, text="Plan routes with jump range:").grid(row=1, column=0, sticky=tk.W, padx=10, pady=4)
    nb.OptionMenu(frame, _Ui.basis_var, _Ui.basis_var.get(), *labels).grid(row=1, column=1, sticky=tk.W)
    nb.Label(frame, text=f"Safety margin (0-{MAX_MARGIN_PCT:g} %):").grid(
        row=2, column=0, sticky=tk.W, padx=10
    )
    nb.EntryMenu(frame, textvariable=_Ui.margin_var, width=6).grid(row=2, column=1, sticky=tk.W)
    nb.Label(
        frame,
        text="Full tank is the safest choice: the range with your current cargo and full tanks.",
    ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 0))
    _Ui.check_var = tk.BooleanVar(value=plugin.settings.update_check)
    nb.Checkbutton(
        frame, text="Tell me when a new version is out (asks GitHub about once a day)", variable=_Ui.check_var
    ).grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 0))
    return frame


def prefs_changed(cmdr: str, is_beta: bool) -> None:
    """Settings dialog closed with OK: validate, save, redraw."""
    plugin = _require_plugin()
    if _Ui.basis_var is None or _Ui.margin_var is None:
        return
    settings = settings_from_form(_Ui.basis_var.get(), _Ui.margin_var.get(), plugin.settings)
    if _Ui.check_var is not None:
        plugin.set_update_check(bool(_Ui.check_var.get()))
        settings = replace(settings, update_check=plugin.settings.update_check)
    if plugin.set_settings(settings):
        _redraw()


def journal_entry(
    cmdr: str,
    is_beta: bool,
    system: str | None,
    station: str | None,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> str | None:
    if _require_plugin().on_journal(entry, state):
        _redraw()
    return None


def dashboard_entry(cmdr: str, is_beta: bool, entry: dict[str, Any]) -> None:
    if _require_plugin().on_dashboard(entry):
        _redraw()


def _require_plugin() -> ScoutPlugin:
    if _Ui.plugin is None:  # EDMC always calls plugin_start3 first; this is only a safety net
        raise RuntimeError("ED Multitool: plugin_start3 was not called")
    return _Ui.plugin


def _open_page() -> None:
    url = _require_plugin().server_url
    if url is None:
        logger.warning("web page unavailable: the server is not running")
        return
    how = open_page(url)
    logger.info("opening %s (%s)", url, how)
    _require_plugin().page_opened(how)
    _redraw()


def _open_update() -> None:
    """The "Version X is out" line was clicked: the release page, in the ordinary browser."""
    url = _require_plugin().update.url
    if url:
        webbrowser.open(url)


def _copy_next() -> None:
    """The "Copy next" button: the followed route's next system onto the clipboard — only on this click."""
    name = _require_plugin().next_name
    if not name or _Ui.frame is None:
        return
    _Ui.frame.clipboard_clear()
    _Ui.frame.clipboard_append(name)
    logger.info("copied the next system to the clipboard: %s", name)


def _pump() -> None:
    """tk timer on the main thread: collect finished background jobs, then re-arm."""
    _Ui.pump_after = None
    if _Ui.plugin is None or _Ui.frame is None:
        return
    try:
        if _Ui.plugin.pump():
            _redraw()
    finally:  # one failing batch must not stop the timer for good
        _Ui.pump_after = _Ui.frame.after(PUMP_MS, _pump)


def _redraw() -> None:
    if _Ui.plugin is None or not _Ui.values:
        return
    text = _Ui.plugin.text
    for key, _label in _ROWS:
        _Ui.values[key]["text"] = getattr(text, key)
    for label, line in ((_Ui.note, text.note), (_Ui.warning, text.warning), (_Ui.update, text.update)):
        if label is not None:
            label["text"] = line
            if line:
                label.grid()
            else:
                label.grid_remove()
    if _Ui.copy_button is not None:
        _Ui.copy_button.state(["!disabled"] if _Ui.plugin.next_name else ["disabled"])
