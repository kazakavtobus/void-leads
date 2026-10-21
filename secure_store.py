"""Windows DPAPI protection for optional separate VK service and user tokens.

A token is encrypted for the current Windows account; no plaintext token is
written to SQLite, settings, logs, or GitHub. On non-Windows persistence fails.
"""
from __future__ import annotations
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Безопасное сохранение доступно только в Windows")
    if not data or len(data) > 10_000:
        raise ValueError("Неверная длина ключа")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    src = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError(ctypes.get_last_error(), "Windows не смог расшифровать VK-токен")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))

def _filename(name: str) -> str:
    if name not in {"vk_token.dpapi", "vk_user_token.dpapi"}:
        raise ValueError("Недопустимое имя защищённого ключа")
    return name


def save_token(folder: Path, token: str, filename: str = "vk_token.dpapi"):
    token = token.strip()
    if not 12 <= len(token) <= 2000:
        raise ValueError("Введи действующий VK-токен")
    folder.mkdir(exist_ok=True, parents=True)
    blob = _dpapi(token.encode("utf-8"), decrypt=False)
    file = folder / _filename(filename)
    temporary = file.with_suffix(".tmp")
    temporary.write_bytes(blob)
    temporary.replace(file)

def load_token(folder: Path, filename: str = "vk_token.dpapi") -> str:
    file = folder / _filename(filename)
    if not file.exists():
        return ""
    return _dpapi(file.read_bytes(), decrypt=True).decode("utf-8")

def delete_token(folder: Path, filename: str = "vk_token.dpapi"):
    (folder / _filename(filename)).unlink(missing_ok=True)
