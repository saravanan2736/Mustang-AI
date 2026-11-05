"""
Terminal access for agent mode: the `run_command` tool.

It is off unless the user turned Terminal on for the chat (ToolContext.terminal), and then every
command is shown to the user (the exact text and the folder) and runs only after they approve it.
Approve-all never covers commands (Proposal.always_ask). This is not a sandbox: an approved command
runs with the user's own rights, so the user's approval is the safeguard. Around it:

- the command runs in the workspace folder (or a folder inside it), with no input and no window;
- it stops after a timeout (default 60 s, at most 300 s) and when the user presses Stop,
  and the whole process tree is stopped, not just the shell;
- the output kept is capped (the start and the end), so one noisy command can't flood the chat;
- the environment passed on has secrets (keys, tokens, passwords) removed;
- a short list of system-level commands is refused even when approved (elevation, shutdown, disk
  formatting, registry/service/user/network configuration, execution policy).
"""

import asyncio
import collections
import os
import re
import subprocess

from app.agent.registry import Proposal, ToolContext, ToolError, ToolSpec, register
from app.agent.workspace import WorkspaceError

COMMAND_TOOL = "run_command"
MAX_COMMAND_CHARS = 4000
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 300
HEAD_CHARS = 12_000
TAIL_CHARS = 8_000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

_running: set = set()  # processes started by run_command, so Stop can end them
_stopped: set[int] = set()  # pids ended by Stop (not by timeout or on their own)

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)

# Never run, even approved. Not a complete list and not a sandbox; see the module docstring.
_REFUSED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brunas\b|-Verb\s+RunAs|\bsudo\b", re.I), "running as another user or as administrator"),
    (re.compile(r"\b(shutdown|Restart-Computer|Stop-Computer)\b", re.I), "shutting down or restarting the computer"),
    (re.compile(r"\bdiskpart\b|\bFormat-Volume\b|\bClear-Disk\b|\bRemove-Partition\b|\bformat(\.com)?\s+[a-z]:", re.I),
     "formatting or partitioning disks"),
    (re.compile(r"\bbcdedit\b|\bSet-ExecutionPolicy\b", re.I), "changing boot or script-execution settings"),
    (re.compile(r"\breg(\.exe)?\s+(add|delete|import|load|unload|restore)\b|\bRemove-ItemProperty\b.*\bHK(LM|CU)\b"
                r"|\bSet-ItemProperty\b.*\bHK(LM|CU)\b|\bNew-ItemProperty\b.*\bHK(LM|CU)\b", re.I),
     "changing the registry"),
    (re.compile(r"\bnet(1)?\s+(user|localgroup)\b|\bsc(\.exe)?\s+(create|delete|config|stop)\b|\bnetsh\b"
                r"|\bschtasks\b|\bNew-Service\b|\bSet-Service\b", re.I),
     "changing users, services, scheduled tasks or network settings"),
    (re.compile(r"\b(rd|rmdir|del|erase|rm|Remove-Item)\b[^\n;|&]*?(?<![\w.\\/-])[a-z]:[\\/]?(?=\s|$|;|\||&)", re.I),
     "deleting a whole drive or its top folder"),
]


def check_command(command: str) -> None:
    """Raise ToolError for an empty, oversized or system-level command."""
    if not command.strip():
        raise ToolError("Give the command to run.")
    if len(command) > MAX_COMMAND_CHARS:
        raise ToolError(f"The command is too long (max {MAX_COMMAND_CHARS} characters).")
    if "\x00" in command:
        raise ToolError("The command contains a null character.")
    for pattern, what in _REFUSED:
        if pattern.search(command):
            raise ToolError(f"Refused: this looks like {what}. Agent mode never runs system-level commands, even when the user approves.")


def clean_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for the command: the user's own, minus anything that looks like a secret."""
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not _SECRET_NAME.search(k) and not k.upper().startswith("MUSTANG_")}


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=_NO_WINDOW, check=False)
    else:
        import signal

        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def kill_all() -> int:
    """Stop every running command (the Stop button). Returns how many were running."""
    procs = list(_running)
    for p in procs:
        _stopped.add(p.pid)
        _kill_tree(p.pid)
    return len(procs)


def _split_chain(command: str) -> tuple[list[str], list[str]]:
    """Split a command line on && and || that are not inside quotes. Returns (segments, operators)."""
    parts, ops, buf = [], [], []
    quote = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote:
            buf.append(c)
            if c == "`" and quote == '"' and i + 1 < len(command):  # PowerShell escape inside double quotes
                buf.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
            buf.append(c)
        elif command[i:i + 2] in ("&&", "||"):
            parts.append("".join(buf))
            ops.append(command[i:i + 2])
            buf = []
            i += 1
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts, ops


def powershell_form(command: str) -> str:
    """Windows PowerShell 5.1 has no && or ||, and models write them all the time (bash habit): turn a chain
    that uses only one of them into the PowerShell equivalent ("a && b" -> "a; if ($?) { b }", nested for
    longer chains so a failure stops the rest). Anything else, including a mix of both, is left as written."""
    parts, ops = _split_chain(command)
    if not ops or len(set(ops)) != 1 or any(not p.strip() for p in parts):
        return command
    cond = "$?" if ops[0] == "&&" else "-not $?"
    out = parts[-1].strip()
    for seg in reversed(parts[:-1][1:]):
        out = f"{seg.strip()}; if ({cond}) {{ {out} }}"
    return f"{parts[0].strip()}; if ({cond}) {{ {out} }}"


def _argv(command: str) -> list[str]:
    if os.name == "nt":
        command = powershell_form(command)
        utf8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Console]::OutputEncoding; "
        # `powershell -Command` turns any failure into exit code 1. Pass a program's own exit code through
        # (builds and tests report their result that way), on a new line so a trailing # comment can't hide it.
        keep_code = "\n$ok = $?; $code = $LASTEXITCODE; if ($code) { exit $code }; if (-not $ok) { exit 1 }"
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", utf8 + "$LASTEXITCODE = 0; " + command + keep_code]
    return ["/bin/sh", "-c", command]


class CommandResult:
    def __init__(self, exit_code: int | None, output: str, total_chars: int, timed_out: bool, stopped: bool):
        self.exit_code, self.output, self.total_chars = exit_code, output, total_chars
        self.timed_out, self.stopped = timed_out, stopped

    def text(self, timeout_s: int) -> str:
        if self.timed_out:
            head = f"The command did not finish within {timeout_s} s and was stopped."
        elif self.stopped:
            head = "The command was stopped before it finished."
        else:
            head = f"Exit code: {self.exit_code}"
        body = self.output.strip("\r\n")
        return f"{head}\n{body}" if body else f"{head}\n(no output)"


async def run_command(command: str, cwd, timeout_s: int = DEFAULT_TIMEOUT_S, env: dict[str, str] | None = None) -> CommandResult:
    popen_kw: dict = {}
    if os.name == "nt":
        popen_kw["creationflags"] = _NO_WINDOW | _NEW_GROUP
    else:
        popen_kw["start_new_session"] = True
    environment = clean_environment(env)
    path_key = next((k for k in environment if k.upper() == "PATH"), "PATH")
    environment[path_key] = environment.get(path_key, "") + os.pathsep + "."  # last: a real tool always wins over a file here
    proc = await asyncio.create_subprocess_exec(
        *_argv(command), cwd=str(cwd), env=environment,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **popen_kw,
    )
    _running.add(proc)
    head: list[str] = []
    head_len = 0
    tail: collections.deque[str] = collections.deque()
    tail_len = 0
    total = 0

    async def read_output():
        nonlocal head_len, tail_len, total
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            total += len(text)
            if head_len < HEAD_CHARS:
                take = text[: HEAD_CHARS - head_len]
                head.append(take)
                head_len += len(take)
                text = text[len(take):]
            if text:
                tail.append(text)
                tail_len += len(text)
                while tail_len - len(tail[0]) >= TAIL_CHARS:
                    tail_len -= len(tail.popleft())

    reader = asyncio.create_task(read_output())
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            await asyncio.to_thread(_kill_tree, proc.pid)
        try:
            await asyncio.wait_for(reader, 5)
        except asyncio.TimeoutError:
            reader.cancel()
    finally:
        _running.discard(proc)
        if proc.returncode is None:
            await asyncio.to_thread(_kill_tree, proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
    stopped = proc.pid in _stopped and not timed_out
    _stopped.discard(proc.pid)
    out_head = "".join(head)
    out_tail = "".join(tail)
    if total > HEAD_CHARS + len(out_tail):
        left_out = total - len(out_head) - len(out_tail)
        output = f"{out_head}\n[... {left_out} characters left out ...]\n{out_tail}"
    else:
        output = out_head + out_tail
    return CommandResult(proc.returncode, output, total, timed_out, stopped)


def _prepare_run(args: dict, ctx: ToolContext) -> Proposal:
    command = args["command"]
    check_command(command)
    timeout_s = int(args.get("timeout_seconds") or DEFAULT_TIMEOUT_S)
    if not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise ToolError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_S}.")
    ws = ctx.workspace
    try:
        folder = ws.resolve(args.get("folder") or ".")
    except WorkspaceError as e:
        raise ToolError(str(e))
    if not folder.is_dir():
        raise ToolError(f"{args.get('folder')!r} is not a folder in the workspace.")
    where = ws.relative(folder)
    one_line = " ".join(command.split())
    summary = f"Run in {where}: {one_line[:140]}{'...' if len(one_line) > 140 else ''}"

    async def apply(call_id: str) -> str:
        result = await run_command(command, folder, timeout_s)
        return result.text(timeout_s)

    shown = f"$ {command}"
    if os.name == "nt" and powershell_form(command) != command:
        shown += f"\n\n(PowerShell has no && or ||; it will run as: {powershell_form(command)})"
    return Proposal(summary=summary, diff=f"{shown}\n\n(folder: {folder}; stops after {timeout_s} s)", apply=apply, always_ask=True)


async def _handler(args: dict, ctx: ToolContext) -> Proposal:
    return _prepare_run(args, ctx)


register(ToolSpec(
    name=COMMAND_TOOL,
    description=("Run one Windows PowerShell 5.1 command and get its output and exit code. It runs INSIDE `folder` "
                 "(default: the workspace top), so paths in the command are relative to that folder. Chain with ; or "
                 "&& (converted for you); programs in the current folder run by name (fizzbuzz.exe) or ./name. "
                 "The user sees the exact command and must approve every run. Use it for builds, tests, git and "
                 "scripts; use the file tools (not the shell) to read or write files. Keep commands short and "
                 "non-interactive. System-level commands are refused."),
    parameters={"type": "object", "properties": {
        "command": {"type": "string", "description": "The PowerShell command line to run"},
        "folder": {"type": "string", "description": "Folder inside the workspace to run in; '.' (default) is the top"},
        "timeout_seconds": {"type": "integer", "description": f"Stop it after this many seconds (default {DEFAULT_TIMEOUT_S}, max {MAX_TIMEOUT_S})",
                            "minimum": 1, "maximum": MAX_TIMEOUT_S},
    }, "required": ["command"]},
    handler=_handler,
    needs_workspace=True,
    needs_terminal=True,
    read_only=False,
    needs_approval=True,
    max_string=MAX_COMMAND_CHARS,
))
