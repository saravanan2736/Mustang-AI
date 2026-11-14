"""
The user's workspace folder: the only place file tools may look.

Every path from the model is checked here before anything is opened: relative paths only,
no "..", no alternate data streams or drive prefixes, no reserved device names, and no
symbolic link or junction anywhere between the workspace root and the target, so nothing
can lead outside the folder the user picked.
"""

import os
import stat
from pathlib import Path, PureWindowsPath

from app.core.security import WINDOWS_RESERVED_NAMES

# Hidden folder in the workspace with backups of files the agent changed (app.agent.changes).
# The agent's tools can't see, read or write it.
BACKUP_DIR_NAME = ".mustang-backups"


class WorkspaceError(ValueError):
    """A path the model asked for is not allowed; the message is shown to the model."""


def _is_reparse_point(path: Path) -> bool:
    """Symbolic links and junctions (Windows reparse points) are never followed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _check_component(part: str) -> None:
    if part in ("", ".", ".."):
        raise WorkspaceError("Paths must stay inside the workspace ('..' is not allowed).")
    if any(c in part for c in ':*?"<>|') or any(ord(c) < 32 for c in part):
        raise WorkspaceError(f"Invalid character in path component {part!r}.")
    if part.rstrip(" .") != part:
        raise WorkspaceError(f"Path components may not end with a space or dot: {part!r}.")
    if part.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        raise WorkspaceError(f"Reserved device name not allowed: {part!r}.")


class Workspace:
    def __init__(self, root: str | Path):
        raw = str(root)
        if "\x00" in raw:
            raise WorkspaceError("Invalid workspace folder.")
        path = Path(raw)
        if not path.is_absolute():
            raise WorkspaceError("The workspace folder must be an absolute path.")
        if path.drive.startswith("\\\\"):
            raise WorkspaceError("Network folders can't be used as a workspace.")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise WorkspaceError("The workspace folder does not exist.")
        if not resolved.is_dir():
            raise WorkspaceError("The workspace must be a folder.")
        if resolved.parent == resolved:
            raise WorkspaceError("A whole drive can't be used as a workspace; pick a folder.")
        for part in resolved.parts[1:]:
            if part.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
                raise WorkspaceError("Invalid workspace folder.")
        self.root = resolved

    def resolve(self, relative: str, *, must_exist: bool = True) -> Path:
        """Path inside the workspace for a model-supplied relative path, or WorkspaceError."""
        if not isinstance(relative, str) or "\x00" in relative or len(relative) > 1024:
            raise WorkspaceError("Invalid path.")
        text = relative.strip().replace("/", "\\")
        if text in ("", ".", ".\\"):
            return self.root
        win = PureWindowsPath(text)
        if win.drive or win.root or text.startswith("\\"):
            raise WorkspaceError("Use a path relative to the workspace, e.g. 'notes.txt' or 'docs\\guide.md'.")
        parts = [p for p in win.parts if p != "."]
        for part in parts:
            _check_component(part)
        if parts and parts[0].lower() == BACKUP_DIR_NAME:
            raise WorkspaceError("That folder holds Mustang AI's backups and is not available to tools.")

        current = self.root
        for part in parts:
            current = current / part
            if _is_reparse_point(current):
                raise WorkspaceError(f"Links and junctions are not followed: {part!r}.")
        if must_exist and not current.exists():
            raise WorkspaceError(f"Not found in the workspace: {relative!r}.")
        # Belt and braces: the fully resolved path must still be inside the root.
        try:
            resolved = current.resolve(strict=must_exist)
            resolved.relative_to(self.root)
        except (ValueError, OSError, RuntimeError):
            raise WorkspaceError("Paths must stay inside the workspace.")
        return resolved

    def relative(self, path: Path) -> str:
        rel = path.relative_to(self.root)
        return "." if str(rel) == "." else str(rel)

    def is_followable(self, path: Path) -> bool:
        """For directory walks: skip links/junctions, the backup folder and anything resolving outside."""
        if _is_reparse_point(path):
            return False
        if path.parent == self.root and path.name.lower() == BACKUP_DIR_NAME:
            return False
        try:
            path.resolve(strict=True).relative_to(self.root)
            return True
        except (ValueError, OSError, RuntimeError):
            return False
