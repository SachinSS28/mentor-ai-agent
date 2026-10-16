"""Terminal chat with Mentor. Conversations persist in Postgres between runs."""

from uuid import uuid4

from mentor.agent.app import open_agent
from mentor.agent.graph import ask

DEFAULT_THREAD = "terminal"


def new_thread_id() -> str:
    return f"terminal-{uuid4().hex[:8]}"


def run_terminal_chat(new_conversation: bool = False, once: str | None = None) -> None:
    thread_id = new_thread_id() if new_conversation else DEFAULT_THREAD
    with open_agent() as graph:
        if once is not None:
            print(ask(graph, once, thread_id))
            return
        print("Mentor (local model). /new starts a fresh conversation, /quit exits.")
        while True:
            try:
                text = input("\nyou › ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if text in ("/quit", "/exit"):
                return
            if text == "/new":
                thread_id = new_thread_id()
                print("Started a new conversation.")
                continue
            if text:
                print(f"\nmentor › {ask(graph, text, thread_id)}")
