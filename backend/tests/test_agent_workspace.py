"""
M5 Phase C: the workspace folder is the only place file tools may look. Every way of naming
something outside it must be refused: "..", absolute and drive paths, UNC paths, alternate data
streams, device names, and links or junctions leading out, directly or further down the path.
"""

import os
import subprocess
from pathlib import Path

import pytest

from app.agent import tools
from app.agent.registry import ToolContext, ToolError
from app.agent.workspace import Workspace, WorkspaceError


@pytest.fixture
def layout(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "docs").mkdir(parents=True)
    (ws / "notes.txt").write_text("Buy milk.\nCall Ravi at 6 pm.\n", encoding="utf-8")
    (ws / "docs" / "guide.md").write_text("# Guide\nHello horses.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    return ws, outside


def _junction(link: Path, target: Path) -> bool:
    """Directory junction (no admin rights needed on Windows)."""
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return r.returncode == 0 and link.exists()


def test_paths_inside_resolve(layout):
    ws_dir, _ = layout
    ws = Workspace(ws_dir)
    assert ws.resolve(".") == ws_dir.resolve()
    assert ws.resolve("notes.txt") == (ws_dir / "notes.txt").resolve()
    assert ws.resolve("docs/guide.md") == ws.resolve("docs\\guide.md") == ws.resolve("./docs/guide.md")


@pytest.mark.parametrize("bad", [
    "..", "../outside/secret.txt", "docs/../../outside/secret.txt", "..\\outside",
    "C:\\Windows\\win.ini", "C:secret.txt", "/etc/passwd", "\\Windows", "\\\\server\\share\\x",
    "notes.txt:hidden", "notes.txt::$DATA", "CON", "con.txt", "NUL", "docs/aux.md", "COM1",
    "notes.txt.", "docs /guide.md", "docs./guide.md", "bad\x00name", "a*b", "what?.txt", "x" * 2000,
])
def test_escapes_and_tricks_are_refused(layout, bad):
    ws = Workspace(layout[0])
    with pytest.raises(WorkspaceError):
        ws.resolve(bad)


def test_missing_file_is_reported(layout):
    with pytest.raises(WorkspaceError, match="Not found"):
        Workspace(layout[0]).resolve("nope.txt")


@pytest.mark.parametrize("root", ["relative\\folder", "C:\\", "\\\\server\\share"])
def test_bad_workspace_roots_are_refused(root):
    with pytest.raises(WorkspaceError):
        Workspace(root)


def test_workspace_root_must_be_an_existing_folder(layout, tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "does-not-exist")
    with pytest.raises(WorkspaceError):
        Workspace(layout[0] / "notes.txt")


def test_junction_out_of_the_workspace_is_never_followed(layout):
    ws_dir, outside = layout
    if not _junction(ws_dir / "escape", outside):
        pytest.skip("could not create a junction here")
    ws = Workspace(ws_dir)
    ctx = ToolContext(workspace=ws)
    for path in ("escape", "escape/secret.txt", "escape\\secret.txt"):
        with pytest.raises(WorkspaceError, match="not followed"):
            ws.resolve(path)
    # Listing and searching skip it; reading through it fails.
    assert "escape" not in tools._list_files(".", ctx)
    assert "TOP SECRET" not in tools._search_text("SECRET", ".", ctx)
    with pytest.raises(ToolError):
        tools._read_file("escape/secret.txt", ctx)


def test_junction_deeper_in_the_tree_is_refused(layout):
    ws_dir, outside = layout
    if not _junction(ws_dir / "docs" / "deep", outside):
        pytest.skip("could not create a junction here")
    with pytest.raises(WorkspaceError, match="not followed"):
        Workspace(ws_dir).resolve("docs/deep/secret.txt")


def test_symlink_to_a_file_outside_is_refused(layout):
    ws_dir, outside = layout
    try:
        os.symlink(outside / "secret.txt", ws_dir / "link.txt")
    except OSError:
        pytest.skip("symbolic links need Developer Mode or admin rights here")
    with pytest.raises(WorkspaceError):
        Workspace(ws_dir).resolve("link.txt")


def test_workspace_given_through_a_junction_is_pinned_to_its_target(layout, tmp_path):
    ws_dir, outside = layout
    alias = tmp_path / "alias"
    if not _junction(alias, ws_dir):
        pytest.skip("could not create a junction here")
    ws = Workspace(alias)
    assert ws.root == ws_dir.resolve()
    with pytest.raises(WorkspaceError):
        ws.resolve("../outside/secret.txt")
