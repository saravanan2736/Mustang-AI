# Terminal access (run_command)

Agent mode can run terminal commands, as a tool the model proposes and the user approves. It is the
step from "reads and edits files" toward a local coding agent, and it is the most sensitive tool in
the app, so it is opt-in at three levels.

## Who decides

1. **Per chat, off by default.** The **Terminal** switch in the prompt bar only appears when agent mode
   is on and a workspace folder is open. Turning it on asks first ("Allow", "Cancel"). The choice is
   saved with the chat (`conversations.terminal_enabled`).
2. **Per command.** The model's call is shown as the exact command line and the folder it will run
   in, with the stop time. Nothing runs until the user presses **Run command**. Deny, Stop, or no answer
   within 10 minutes leaves the machine untouched.
3. **Never by "Approve all".** Approve-all covers file changes only (`Proposal.always_ask`). It is
   ignored for commands, in the loop and in the UI (the button is not shown on a command card).

The setting comes from the UI's request (`agent.terminal`), like the workspace. The model cannot turn
it on: without it, `run_command` is not offered and a call to it is an "unknown tool".

## What it does

- Runs one Windows PowerShell command (`-NoProfile -NonInteractive`), in the workspace folder or a
  folder inside it. No input (stdin is closed), no console window.
- Stops after a timeout (default 60 s, at most 300 s) and on **Stop**; the whole process tree is
  ended with `taskkill /T /F`, not only the shell.
- Keeps the start (12,000 chars) and the end (8,000 chars) of the output; the rest is dropped while
  reading, so a noisy command cannot use up memory or fill the chat.
- Reports the program's own exit code (PowerShell's `-Command` mode would turn every failure into 1).
- Passes on the user's environment minus anything that looks like a secret (`KEY`, `TOKEN`,
  `SECRET`, `PASSWORD`, `CREDENTIAL`, `AUTH`) and the app's own `MUSTANG_*` variables.
- Refuses, even when approved: running as administrator or another user, shutdown/restart, disk
  formatting and partitioning, boot and execution-policy settings, registry changes, user, service,
  scheduled-task and network configuration, and deleting a whole drive or its top folder.

## What it does not do

It is **not a sandbox**. An approved command runs with the user's own rights and can do anything
those rights allow, inside or outside the workspace (for example `Remove-Item` on any folder the
user can write to). The refusal list is a seat belt for obvious mistakes, not a security boundary:
it is a handful of patterns and can be bypassed by a determined command. The user's approval of the
exact text is the safeguard, which is why the card shows the full command and why commands are
never auto-approved.

Command output is untrusted data, like every tool result: the agent prompt tells the model not to
follow instructions that appear in it. A model that has read a hostile file could still *propose* a
bad command; the user is the one who reads and approves it.

## Tests

`backend/tests/test_agent_terminal.py` runs real PowerShell: output, exit codes, UTF-8, timeout, the
output cap, secret removal, closed stdin, Stop, and the approval rules above.

## Ideas for later (not built)

- A per-command "always allow this exact command in this chat" list.
- Streaming output into the card while the command runs.
- Running inside a Windows sandbox or a restricted token instead of the user's own session.
