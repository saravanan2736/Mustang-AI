"""
File changes made by agent mode, and how to undo them.

Every approved change is recorded in the workspace's hidden backup folder: the previous version
of an edited file is copied there first, and a journal entry keeps the SHA-256 of what was
written. Undo puts the previous version back (or removes a file the agent created), but only
if the file is still exactly what the agent wrote, so the user's later edits are never lost.
The folder is invisible to the agent's tools (app.agent.workspace).
"""

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.agent.workspace import BACKUP_DIR_NAME, Workspace

JOURNAL_NAME = "journal.json"
_lock = threading.Lock()


class ChangeError(Exception):
    """A change or undo that can't be done; the message is shown to the user or the model."""


@dataclass
class Change:
    call_id: str
    conversation_id: str | None
    path: str  # relative to the workspace
    kind: str  # "create" | "edit"
    backup: str | None  # file name in the backup folder (edits only)
    new_sha256: str
    time: float
    undone: bool = False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError:
        return None


def _backup_dir(ws: Workspace) -> Path:
    d = ws.root / BACKUP_DIR_NAME
    d.mkdir(exist_ok=True)
    if os.name == "nt":
        # Hidden in Explorer, like other app folders; failure to set the attribute is harmless.
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(d), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass
    return d


def _journal_path(ws: Workspace) -> Path:
    return _backup_dir(ws) / JOURNAL_NAME


def _read_journal(ws: Workspace) -> list[Change]:
    p = ws.root / BACKUP_DIR_NAME / JOURNAL_NAME
    try:
        return [Change(**e) for e in json.loads(p.read_text(encoding="utf-8"))]
    except FileNotFoundError:
        return []
    except (ValueError, TypeError):
        raise ChangeError("The change journal in the workspace's backup folder is damaged.")


def _write_journal(ws: Workspace, entries: list[Change]) -> None:
    p = _journal_path(ws)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(e) for e in entries], indent=1), encoding="utf-8")
    os.replace(tmp, p)


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary file in the same folder, so a crash never leaves half a file."""
    tmp = path.with_name(f".{path.name}.mustang-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply_change(ws: Workspace, target: Path, data: bytes, *, kind: str, call_id: str,
                 conversation_id: str | None, expected_sha256: str | None) -> Change:
    """Write `data` to `target` after the user approved it. `expected_sha256` is the file as it
    was when the change was shown (None = it must not exist): if it changed since, nothing is
    written."""
    with _lock:
        current = sha256_file(target)
        if current != expected_sha256:
            raise ChangeError("The file changed after the change was shown; nothing was written. Ask again.")
        backup_name = None
        if kind == "edit":
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup_name = f"{stamp}-{call_id[-12:]}-{target.name}"
            (_backup_dir(ws) / backup_name).write_bytes(target.read_bytes())
        atomic_write(target, data)
        entry = Change(call_id=call_id, conversation_id=conversation_id, path=ws.relative(target), kind=kind,
                       backup=backup_name, new_sha256=sha256_bytes(data), time=time.time())
        entries = _read_journal(ws)
        entries.append(entry)
        _write_journal(ws, entries)
        return entry


def list_changes(ws: Workspace, conversation_id: str | None = None) -> list[dict]:
    """Changes in this workspace (optionally one chat's), each with whether Undo is possible now."""
    out = []
    for e in _read_journal(ws):
        if conversation_id is not None and e.conversation_id != conversation_id:
            continue
        current = sha256_file(ws.root / e.path)
        out.append({**asdict(e), "can_undo": not e.undone and current == e.new_sha256})
    return out


def undo_change(ws: Workspace, call_id: str, conversation_id: str | None = None) -> Change:
    with _lock:
        entries = _read_journal(ws)
        entry = next((e for e in entries if e.call_id == call_id
                      and (conversation_id is None or e.conversation_id == conversation_id)), None)
        if entry is None:
            raise ChangeError("No change with that id in this workspace.")
        if entry.undone:
            raise ChangeError("This change was already undone.")
        target = ws.resolve(entry.path, must_exist=False)
        if sha256_file(target) != entry.new_sha256:
            raise ChangeError("The file was changed after the agent wrote it; undo would lose those edits, so nothing was changed.")
        if entry.kind == "edit":
            backup = ws.root / BACKUP_DIR_NAME / (entry.backup or "")
            if not entry.backup or not backup.is_file():
                raise ChangeError("The backup of the previous version is missing.")
            atomic_write(target, backup.read_bytes())
        else:
            target.unlink()
        entry.undone = True
        _write_journal(ws, entries)
        return entry
