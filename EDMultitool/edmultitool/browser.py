"""Open the ED Multitool page in a frameless app window (`--app=`), else in the default browser.

App windows need a Chromium browser. Preference: the user's default browser when it is Chromium-based
(Edge, Chrome, Brave), then any installed one of those, else a normal tab in the default browser.
Edge is uninstallable in the EEA, so it cannot be assumed. Registry and disk access is read-only.
"""

import os
import shlex
import subprocess
import sys
import webbrowser
from pathlib import Path

APP_WINDOW_SIZE = (1280, 860)
_APP_BROWSERS = {  # exe name → install folder under Program Files / LocalAppData
    "msedge.exe": Path("Microsoft", "Edge", "Application"),
    "chrome.exe": Path("Google", "Chrome", "Application"),
    "brave.exe": Path("BraveSoftware", "Brave-Browser", "Application"),
}
_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_URL_ASSOC_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http"
# Where Windows records the default browser's ProgId: newer Windows 11 builds keep it in a subkey of
# UserChoiceLatest; older builds (and the legacy mirror) in UserChoice.
_PROG_ID_KEYS = (rf"{_URL_ASSOC_KEY}\UserChoiceLatest\ProgId", rf"{_URL_ASSOC_KEY}\UserChoice")


def _reg_str(hive: str, path: str, value: str = "") -> str | None:
    """One string value from the registry ("HKCU" | "HKLM" | "HKCR"), or None. Read-only."""
    import winreg

    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}.get(
        hive, winreg.HKEY_CLASSES_ROOT
    )
    try:
        with winreg.OpenKey(root, path) as key:
            data, _ = winreg.QueryValueEx(key, value)
    except OSError:
        return None
    return data if isinstance(data, str) and data else None


def _default_browser_exe() -> str | None:
    prog_id = next((p for k in _PROG_ID_KEYS if (p := _reg_str("HKCU", k, "ProgId"))), None)
    if prog_id is None:
        return None
    command = _reg_str("HKCR", rf"{prog_id}\shell\open\command")
    if command is None:
        return None
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    return parts[0].strip('"') if parts else None


def _registered_exe(name: str) -> str | None:
    for hive in ("HKCU", "HKLM"):
        path = _reg_str(hive, rf"{_APP_PATHS_KEY}\{name}")
        if path is not None:
            return path.strip('"')
    return None


def find_app_browser() -> str | None:
    """Path of a Chromium browser that can open an app window, or None."""
    if sys.platform != "win32":
        return None
    candidates: list[str] = []
    default = _default_browser_exe()
    if default and Path(default).name.lower() in _APP_BROWSERS:
        candidates.append(default)
    for name, folder in _APP_BROWSERS.items():
        registered = _registered_exe(name)
        if registered:
            candidates.append(registered)
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(str(Path(base) / folder / name))
    return next((c for c in candidates if Path(c).is_file()), None)


def app_window_command(browser: str, url: str) -> list[str]:
    width, height = APP_WINDOW_SIZE
    return [browser, f"--app={url}", f"--window-size={width},{height}"]


def open_page(url: str, browser: str | None = None) -> str:
    """Open `url`; returns how it was opened ("app-window" | "browser" | "failed"). Never raises."""
    browser = browser if browser is not None else find_app_browser()
    if browser is not None:
        try:
            subprocess.Popen(
                app_window_command(browser, url),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return "app-window"
        except OSError:
            pass
    try:
        return "browser" if webbrowser.open(url) else "failed"
    except webbrowser.Error:
        return "failed"
