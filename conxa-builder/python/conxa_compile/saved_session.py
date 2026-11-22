"""The one way Studio reads and writes a saved sign-in (Playwright storageState) on disk (SG-27).

Each file is locked with Windows' own per-user protection (DPAPI, the store behind Credential Manager
and Electron's safeStorage): only this Windows account on this machine can open it, and there is no
key for Conxa to keep. The file keeps its name and stays JSON: ``{"conxa_sealed_session": 1, "dpapi": "<base64>"}``.

A plain storageState file (written before this existed, or where the lock is unavailable) still reads.
``seal_plain_sessions`` converts those at startup, and removes a plain copy only once the locked copy
has been read back to identical content.

A locked file that cannot be opened here (made on another machine or Windows account, or damaged)
raises ``SavedSessionUnreadable``, a ValueError, so every reader that already treats a corrupt file
as "signed out" asks the person to sign in again.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MARK = "conxa_sealed_session"
UNREADABLE = "This saved sign-in can't be opened on this computer. Sign in again."
_ENTROPY = b"conxa-studio-saved-session-v1"


class SavedSessionUnreadable(ValueError):
    pass


def _dpapi(protect: bool, data: bytes) -> bytes:
    if sys.platform != "win32":
        # shortcut: Studio ships for Windows only, so elsewhere files stay plain; add keyring (Keychain /
        # Secret Service) holding an AES-256-GCM key, as the runtime does, when a macOS/Linux build ships.
        raise OSError("Windows protection is not available on this system")
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes, keep: list) -> _Blob:
        buf = ctypes.create_string_buffer(b, len(b))
        keep.append(buf)
        return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    keep: list = []
    inp, ent, out = blob(data, keep), blob(_ENTROPY, keep), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a prompt from a background backend.
    if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _seal(raw: bytes) -> str:
    """The locked file body for `raw`, proven to open back to exactly `raw`. OSError when it cannot be."""
    blob = _dpapi(True, raw)
    if _dpapi(False, blob) != raw:
        raise OSError("the locked copy did not open back to the same sign-in")
    return json.dumps({MARK: 1, "dpapi": base64.b64encode(blob).decode("ascii")})


def _open(data: Any) -> Any:
    if not (isinstance(data, dict) and MARK in data):
        return data  # a plain storageState
    try:
        return json.loads(_dpapi(False, base64.b64decode(data["dpapi"])))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SavedSessionUnreadable(UNREADABLE) from exc


def read_state(path: str | Path) -> dict[str, Any]:
    """The storageState saved at `path`. OSError if missing; ValueError (SavedSessionUnreadable for a
    locked file that cannot be opened here) if it cannot be read."""
    return _open(json.loads(Path(path).read_text(encoding="utf-8")))


def write_state(path: str | Path, state: dict[str, Any]) -> None:
    """Save `state` at `path`, locked. Where the lock fails it is saved plain and a warning is logged,
    as the runtime does (SG-11): losing a sign-in the person just made would be worse."""
    path = Path(path)
    raw = json.dumps(state).encode("utf-8")
    try:
        body = _seal(raw)
    except OSError as exc:
        if sys.platform == "win32":
            log.warning("A saved sign-in could not be locked, so it was saved unlocked on this computer (%s): %s", exc, path)
        body = raw.decode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def seal_plain_sessions(data_dir: str | Path) -> list[Path]:
    """Lock every plain saved sign-in under `data_dir` in place. The plain file is replaced only after
    the locked copy on disk reads back identical; on any failure the plain file is kept as it was and a
    warning says so. Returns the files left plain. Run before any command is served (no writer races)."""
    if sys.platform != "win32":
        return []
    root = Path(data_dir)
    kept: list[Path] = []
    paths = [*root.glob("groups/*/auth/*"), *root.glob("workflows/*/merged_group_state.json")]
    for path in paths:
        if not path.is_file() or path.name.endswith(".tmp"):
            continue  # a .tmp is a write that never finished; the real file beside it is intact
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, ValueError):
            continue  # not a file we understand: leave it exactly as it is
        if not isinstance(data, dict) or MARK in data:
            continue
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(_seal(raw), encoding="utf-8")
            if read_state(tmp) != data:
                raise OSError("the locked copy did not read back the same")
            os.replace(tmp, path)
        except (OSError, ValueError) as exc:
            tmp.unlink(missing_ok=True)
            kept.append(path)
            log.warning("A saved sign-in could not be locked and was left unlocked, unchanged (%s): %s", exc, path)
    return kept
