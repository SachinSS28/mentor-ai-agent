"""Small facts Mentor keeps between restarts (the app_state table), e.g. when a digest was sent."""

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from mentor.db.models import AppState
from mentor.db.session import get_sessionmaker


def get_state(key: str) -> str | None:
    with get_sessionmaker()() as session:
        state = session.get(AppState, key)
        return state.value if state else None


def set_state(key: str, value: str) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            insert(AppState)
            .values(key=key, value=value)
            .on_conflict_do_update(
                index_elements=[AppState.key], set_={"value": value, "updated_at": func.now()}
            )
        )
        session.commit()
