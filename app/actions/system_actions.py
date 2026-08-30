"""System-level actions: volume, media, launching things."""
from __future__ import annotations

import logging
import os
import subprocess
import webbrowser

from .input_win import key_press

log = logging.getLogger(__name__)


def volume_up(steps: int = 2) -> None:
    for _ in range(max(1, steps)):
        key_press("volumeup")


def volume_down(steps: int = 2) -> None:
    for _ in range(max(1, steps)):
        key_press("volumedown")


def volume_mute() -> None:
    key_press("volumemute")


def media_play_pause() -> None:
    key_press("mediaplaypause")


def media_next() -> None:
    key_press("medianext")


def media_prev() -> None:
    key_press("mediaprev")


def validate_launch(path: str, cwd: str = "",
                    if_running: str = "new") -> str | None:
    """Validate launch parameters. Returns an error message or None.

    Security: the path is untrusted configuration — it must point at an
    existing .exe file; no shell is ever involved.
    """
    if not path or not path.strip():
        return "an executable path is required"
    p = path.strip().strip('"')
    if not os.path.isfile(p):
        return f"executable not found: {p}"
    if not p.lower().endswith(".exe"):
        return "only .exe files can be launched (no scripts/shell)"
    if cwd and not os.path.isdir(cwd):
        return f"working directory not found: {cwd}"
    if if_running not in ("new", "focus"):
        return f"bad if_running value: {if_running!r}"
    return None


def parse_launch_args(args: str) -> list[str]:
    """Split an argument string safely (quotes respected, no shell)."""
    if not args or not args.strip():
        return []
    import shlex
    tokens = shlex.split(args, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t
            for t in tokens]


def launch_app(path: str, args: str = "", cwd: str = "",
               if_running: str = "new") -> None:
    """Launch a Windows executable directly (never via a shell).

    if_running="focus": if a visible window of the same executable
    exists, bring it to the foreground instead of launching again.
    """
    err = validate_launch(path, cwd, if_running)
    if err:
        raise ValueError(err)
    p = path.strip().strip('"')
    if if_running == "focus":
        from .windows_ctl import focus_process
        if focus_process(os.path.basename(p).lower()):
            log.info("Focused existing instance of %s", p)
            return
    cmd = [p] + parse_launch_args(args)
    subprocess.Popen(cmd, shell=False, cwd=(cwd or None))
    log.info("Launched %s", p)


def validate_url(url: str) -> str | None:
    """Validate an untrusted configured URL. http/https only — never a
    shell, never file:/protocol handlers."""
    if not url or not url.strip():
        return "a URL is required"
    from urllib.parse import urlparse
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return f"invalid URL: {url!r}"
    if parts.scheme not in ("http", "https"):
        return "URL must start with http:// or https://"
    if not parts.netloc:
        return f"invalid URL: {url!r}"
    return None


def open_url(url: str) -> None:
    err = validate_url(url)
    if err:
        raise ValueError(err)
    webbrowser.open(url.strip())


def get_clipboard_text() -> str:
    """Read clipboard text (empty string when no text is present).
    Accessed only when a workflow step explicitly asks — never
    monitored."""
    import win32clipboard
    for _ in range(2):   # clipboard can be briefly locked by other apps
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(
                        win32clipboard.CF_UNICODETEXT):
                    return str(win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT))
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            import time
            time.sleep(0.05)
    return ""


def set_clipboard_text(text: str) -> None:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT,
                                        str(text))
    finally:
        win32clipboard.CloseClipboard()


def open_folder(path: str) -> None:
    if not path or not os.path.isdir(path):
        raise ValueError(f"open_folder: not a directory: {path!r}")
    os.startfile(path)  # noqa: S606 — intended shell open
