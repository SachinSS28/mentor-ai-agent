from datetime import UTC, datetime

from mentor.db.models import Memory
from mentor.mcp_servers.mentor_core.memory import rank


def saved(fact: str, day: int) -> Memory:
    return Memory(fact=fact, layer="profile", updated_at=datetime(2026, 10, day, tzinfo=UTC))


def test_recall_picks_matching_memories_when_not_all_fit() -> None:
    cv = saved("CV: product manager, MBA", 1)
    tea = saved("Likes green tea", 3)
    assert rank([tea, cv], "what's on my CV?", budget=30) == [cv]
    assert rank([tea, cv], "", budget=30) == [tea]  # no match: the newest


def test_recall_lists_what_it_picked_oldest_first() -> None:
    parts = [saved(f"CV, part {n} of 3", n) for n in (1, 2, 3)]
    assert rank(list(reversed(parts)), "CV") == parts


def test_recall_always_returns_something() -> None:
    long_fact = saved("x" * 50, 1)
    assert rank([long_fact], "", budget=10) == [long_fact]
