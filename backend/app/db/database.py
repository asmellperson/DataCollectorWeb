from datetime import datetime, timezone
from sqlalchemy import create_engine, event, String, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from app.core.config import STATE_ROOT

engine = create_engine(
    f"sqlite:///{STATE_ROOT / 'index.sqlite3'}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _):
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")


Session = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    task_type: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)


class Sample(Base):
    __tablename__ = "samples"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    image: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending", index=True)
    width: Mapped[int]
    height: Mapped[int]
    created_at: Mapped[str] = mapped_column(default=now)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    url: Mapped[str]
    transport: Mapped[str] = mapped_column(default="tcp")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    total: Mapped[int] = mapped_column(default=0)
    processed: Mapped[int] = mapped_column(default=0)
    success: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(default=now)


Base.metadata.create_all(engine)
