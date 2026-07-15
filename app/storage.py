from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine

from .config import DATABASE_URL


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UsageRecord(SQLModel, table=True):
    """One metered LLM call — the gateway's raw ledger."""

    id: int | None = Field(default=None, primary_key=True)
    tenant: str = Field(index=True)
    model: str = Field(index=True)
    provider: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    image_tokens: int = 0
    capacity_units: str  # Decimal serialized as string — exact, not float
    created_at: datetime = Field(default_factory=utcnow, index=True)


class BilledUsage(SQLModel, table=True):
    """Aggregated rows 'sent to the billing service' by the billing job."""

    id: int | None = Field(default=None, primary_key=True)
    tenant: str
    model: str
    window_start: datetime
    window_end: datetime
    record_count: int
    capacity_units: str


class BillingState(SQLModel, table=True):
    """Watermark for the idempotent billing job (single row, id=1).

    Advanced ONLY after a successful send: crash mid-run => next run
    replays the same window; nothing is lost, nothing double-billed.
    """

    id: int = Field(default=1, primary_key=True)
    last_processed_at: datetime


_is_memory = DATABASE_URL in ("sqlite://", "sqlite:///:memory:")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    poolclass=StaticPool if _is_memory else None,
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
