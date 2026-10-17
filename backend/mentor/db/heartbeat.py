"""Each copy of the Telegram bot records that it's alive; the Mac's standby reads the cloud's."""

from datetime import timedelta

from sqlalchemy import Engine, Interval, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from mentor.db.models import Heartbeat
from mentor.settings import Host


def beat(engine: Engine, host: Host) -> None:
    statement = insert(Heartbeat).values(host=host, seen_at=func.now())
    statement = statement.on_conflict_do_update(
        index_elements=[Heartbeat.host], set_={"seen_at": func.now()}
    )
    with engine.begin() as conn:
        conn.execute(statement)


def silence(engine: Engine, host: Host) -> timedelta | None:
    """How long since `host` last beat, by the database's clock; None if it never has."""
    statement = select(cast(func.now() - Heartbeat.seen_at, Interval)).where(Heartbeat.host == host)
    with engine.connect() as conn:
        return conn.execute(statement).scalar()
