from mentor.db.models import EMBEDDING_DIMENSIONS, Base, Memory

EXPECTED_TABLES = {
    "workspaces",
    "goals",
    "tasks",
    "routines",
    "people",
    "places",
    "meetings",
    "trips",
    "expenses",
    "memories",
    "approvals",
    "audit_log",
    "notion_sync",
    "llm_usage",
    "jobs",
    "processed_emails",
    "app_state",
    "briefing_items",
    "heartbeats",
    "research_highlights",
}


def test_every_planned_table_is_modelled() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_status_columns_have_named_check_constraints() -> None:
    names = {c.name for c in Base.metadata.tables["tasks"].constraints}
    assert {"ck_tasks_status", "ck_tasks_source", "ck_tasks_priority"} <= names


def test_memory_embedding_matches_the_embedding_model() -> None:
    assert Memory.__table__.c.embedding.type.dim == EMBEDDING_DIMENSIONS
