"""Installed-application discovery for the launch action UI.

Sources (no hard-coded install paths):
1. Windows "App Paths" registry keys (HKLM + HKCU, 64/32-bit views) —
   covers Office, Chrome, Edge and most classic desktop installers.
2. Well-known executables in %WINDIR%/System32 (notepad, calc, paint,
   explorer) — calc.exe is the Windows alias stub, which transparently
   opens the modern Calculator.

UWP/MSIX packaged apps that have no App Paths entry are NOT discovered
and are not launchable via a plain .exe path — documented limitation.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_SYSTEM_BASICS = ["notepad.exe", "calc.exe", "mspaint.exe", "explorer.exe"]


@dataclass(frozen=True)
class DiscoveredApp:
    name: str   # friendly display name, e.g. "Excel"
    path: str   # absolute executable path


def _friendly(exe_name: str) -> str:
    stem = os.path.splitext(exe_name)[0]
    special = {"winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
               "outlook": "Outlook", "msaccess": "Access", "mspub": "Publisher",
               "onenote": "OneNote", "mspaint": "Paint", "calc": "Calculator",
               "explorer": "File Explorer", "msedge": "Microsoft Edge"}
    return special.get(stem.lower(), stem.capitalize())


def discover_applications() -> list[DiscoveredApp]:
    apps: dict[str, str] = {}
    try:
        import winreg
        views = [winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                 winreg.KEY_READ | winreg.KEY_WOW64_32KEY]
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for access in views:
                try:
                    key = winreg.OpenKey(root, _APP_PATHS, 0, access)
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(key, i)
                        except OSError:
                            break
                        i += 1
                        if not sub.lower().endswith(".exe"):
                            continue
                        try:
                            with winreg.OpenKey(key, sub, 0, access) as sk:
                                path, _ = winreg.QueryValueEx(sk, None)
                        except OSError:
                            continue
                        if not path:
                            continue
                        path = str(path).strip().strip('"')
                        if os.path.isfile(path):
                            apps.setdefault(sub.lower(), path)
                finally:
                    winreg.CloseKey(key)
    except Exception:
        log.exception("App Paths discovery failed")

    windir = os.environ.get("WINDIR", r"C:\Windows")
    for exe in _SYSTEM_BASICS:
        p = os.path.join(windir, "System32", exe)
        if exe == "explorer.exe":
            p = os.path.join(windir, exe)
        if os.path.isfile(p):
            apps.setdefault(exe, p)

    out = [DiscoveredApp(_friendly(exe), path)
           for exe, path in apps.items()]
    out.sort(key=lambda a: a.name.lower())
    log.info("Discovered %d launchable application(s)", len(out))
    return out
