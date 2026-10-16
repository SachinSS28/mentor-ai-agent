"""Read-only tools for the hello-world agent (step 0.9).

From Phase 1 these move into the mentor-core MCP server behind Sentinel.
"""

from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from mentor.charter import owner_timezone
from mentor.db.models import Task
from mentor.db.session import get_sessionmaker


def format_task(task: Task, tz: ZoneInfo) -> str:
    details = [f"P{task.priority}"]
    if task.due is not None:
        details.insert(0, f"due {task.due.astimezone(tz):%a %d %b %H:%M}")
    if task.workspace_id:
        details.append(task.workspace_id)
    return f"- {task.title} ({', '.join(details)})"


@tool
def list_open_tasks(limit: int = 10) -> str:
    """List the owner's open tasks, soonest due first.

    Use this for any question about tasks, to-dos, priorities or what is pending.
    """
    query = (
        select(Task)
        .where(Task.status == "open")
        .order_by(Task.due.asc().nulls_last(), Task.priority)
        .limit(max(1, min(limit, 50)))
    )
    with get_sessionmaker()() as session:
        tasks = session.scalars(query).all()
    if not tasks:
        return "No open tasks."
    tz = owner_timezone()
    return "\n".join(format_task(t, tz) for t in tasks)


READ_ONLY_TOOLS: list[BaseTool] = [list_open_tasks]
