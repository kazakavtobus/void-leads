"""Windows tray icon, background mode and system notifications.

Tray failures never stop the core local app. No unsolicited auto-start:
the user opts in explicitly in Settings.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

APP_ID = "VOID LEADS"

def is_startup_enabled():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except FileNotFoundError:
        return False

def set_startup(enabled: bool):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("Автозапуск доступен после установки Windows-версии")
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = f'"{Path(sys.executable).resolve()}" --no-browser'
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass
    return is_startup_enabled()

class Tray:
    def __init__(self, open_window, stop_app, image_path: Path):
        self.icon = None
        self.open_window = open_window
        self.stop_app = stop_app
        self.image_path = image_path

    def start(self):
        if os.name != "nt":
            return False
        try:
            import pystray
            from PIL import Image
            if not self.image_path.is_file():
                return False
            icon = pystray.Icon(
                "VOID LEADS", Image.open(self.image_path), "VOID LEADS · поиск клиентов",
                menu=pystray.Menu(
                    pystray.MenuItem("Открыть VOID LEADS", lambda _icon,_item: self.open_window(), default=True),
                    pystray.MenuItem("Выйти из программы", lambda _icon,_item: self.stop_app())
                )
            )
            icon.run_detached()
            self.icon = icon
            return True
        except Exception:
            return False

    def notify(self, title: str, message: str) -> bool:
        if self.icon is None:
            return False
        try:
            self.icon.notify(message[:220], title=title[:100])
            return True
        except Exception:
            return False

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
