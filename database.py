from datetime import datetime

from sqlalchemy import Float, String, DateTime
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings

DATABASE_URL = settings.database_url

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Device(Base):
    """
    A registered vehicle dongle. Each device gets a unique API key
    (hashed before storage) used to authenticate its telemetry posts.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vin: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_api_key: Mapped[str] = mapped_column(String)


class TelemetryReading(Base):
    """
    Database table storing each telemetry reading received from a vehicle.
    This is the persisted version of schemas.TelemetryPayload.
    """

    __tablename__ = "telemetry_readings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vin: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)

    battery_voltage: Mapped[float] = mapped_column(Float)
    state_of_charge: Mapped[float] = mapped_column(Float)
    kwh_consumed: Mapped[float] = mapped_column(Float)

    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    speed_kmh: Mapped[float] = mapped_column(Float)

    confidence_score: Mapped[float] = mapped_column(Float, default=100.0)
    flags: Mapped[str] = mapped_column(String, default="")  # comma-separated flag names

    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    e_base_kg: Mapped[float] = mapped_column(Float, default=0.0)
    e_project_kg: Mapped[float] = mapped_column(Float, default=0.0)
    e_avoided_kg: Mapped[float] = mapped_column(Float, default=0.0)

    payload_hash: Mapped[str] = mapped_column(String)
    chain_hash: Mapped[str] = mapped_column(String)
    verifier_notes: Mapped[str | None] = mapped_column(String, nullable=True)


async def init_db():
    """Create tables if they don't exist yet. Equivalent to running Django migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session():
    """Dependency that provides a DB session per-request, like Django's request-scoped ORM access."""
    async with async_session() as session:
        yield session
