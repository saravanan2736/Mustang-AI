"""
The agent's task list (`update_tasks`): a short plan the model writes down and keeps current while it
works on something with several steps. It is shown to the user above the prompt box, saved with the
chat, and put back into the agent's instructions on every step, so it survives the history being
trimmed to fit a small context window.
"""

import json
import logging

from app.agent.registry import ToolContext, ToolError, ToolSpec, register

logger = logging.getLogger("mustang.agent")

MAX_TASKS = 20
MAX_TASK_CHARS = 200
STATUSES = ("pending", "in_progress", "done")
_MARK = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}


def normalise(tasks: list[dict]) -> list[dict]:
    """Check and clean a task list from the model: text trimmed, known statuses, at most one in progress."""
    if len(tasks) > MAX_TASKS:
        raise ToolError(f"Keep the list to {MAX_TASKS} tasks or fewer; merge small steps.")
    out = []
    for i, t in enumerate(tasks, 1):
        text = " ".join(str(t.get("text", "")).split())
        if not text:
            raise ToolError(f"Task {i} has no text.")
        status = t.get("status", "pending")
        if status not in STATUSES:
            raise ToolError(f"Task {i}: status must be one of {', '.join(STATUSES)}.")
        out.append({"text": text[:MAX_TASK_CHARS], "status": status})
    if sum(1 for t in out if t["status"] == "in_progress") > 1:
        raise ToolError("Only one task can be in_progress at a time. Mark the others pending or done.")
    return out


def format_tasks(tasks: list[dict]) -> str:
    return "\n".join(f"{_MARK.get(t['status'], '[ ]')} {t['text']}" for t in tasks)


def prompt_section(tasks: list[dict]) -> str:
    """The task list as the agent sees it at the start of every step."""
    if not tasks:
        return ""
    return ("Your task list for this chat (keep it current with update_tasks; [x] done, [~] in progress, [ ] to do):\n"
            + format_tasks(tasks))


async def save_tasks(conversation_id: str | None, tasks: list[dict]) -> None:
    """Store the list with the chat (a chat that isn't saved yet keeps it only for this reply)."""
    if not conversation_id:
        return
    from app.db.database import get_db

    db = await get_db()
    try:
        await db.execute("UPDATE conversations SET tasks = ? WHERE id = ?", (json.dumps(tasks), conversation_id))
        await db.commit()
    finally:
        await db.close()


async def load_tasks(conversation_id: str | None) -> list[dict]:
    if not conversation_id:
        return []
    from app.db.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT tasks FROM conversations WHERE id = ?", (conversation_id,))
    finally:
        await db.close()
    try:
        data = json.loads(rows[0]["tasks"]) if rows and rows[0]["tasks"] else []
        return [t for t in data if isinstance(t, dict) and t.get("text")]
    except (ValueError, TypeError):
        return []


async def _update_tasks(args: dict, ctx: ToolContext) -> str:
    tasks = normalise(args["tasks"])
    ctx.tasks = tasks
    try:
        await save_tasks(ctx.conversation_id, tasks)
    except Exception:  # noqa: BLE001  (the list still works for this reply)
        logger.exception("Could not save the task list")
    if not tasks:
        return "Task list cleared."
    done = sum(1 for t in tasks if t["status"] == "done")
    nxt = next((t for t in tasks if t["status"] != "done"), None)
    tail = f" Next: {nxt['text']}" if nxt else " All tasks are done. Now tell the user briefly what you did."
    return f"Task list saved: {done} of {len(tasks)} done.{tail}"


register(ToolSpec(
    name="update_tasks",
    description=("Write or update your task list for work with several steps. Send the whole list every time: each "
                 "item has text and a status (pending, in_progress, done). Write the plan first, keep exactly one "
                 "item in_progress, and mark items done as you finish them. Skip it for simple questions."),
    parameters={"type": "object", "properties": {
        "tasks": {"type": "array", "maxItems": MAX_TASKS, "description": "The full task list, in order",
                  "items": {"type": "object", "properties": {
                      "text": {"type": "string", "description": "What to do, in a few words"},
                      "status": {"type": "string", "enum": list(STATUSES)},
                  }, "required": ["text", "status"]}},
    }, "required": ["tasks"]},
    handler=_update_tasks,
    max_string=MAX_TASK_CHARS * 2,
))
