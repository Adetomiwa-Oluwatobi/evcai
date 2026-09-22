from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    """
    Centralized config. Values are read from environment variables,
    or from a .env file in the project root during local development.
    This is the FastAPI equivalent of Django's settings.py reading
    from os.environ, but with type validation built in.
    """

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/telemetry_db"
    admin_api_key: str = "CHANGE-ME-IN-PRODUCTION"
    enable_simulator_routes: bool = True

    model_config = SettingsConfigDict(env_file=".env")

    @field_validator("database_url")
    @classmethod
    def force_asyncpg_driver(cls, v: str) -> str:
        """
        Hosting platforms (Render, Heroku, Railway, etc.) commonly hand you
        a plain 'postgres://' or 'postgresql://' connection string, which
        makes SQLAlchemy default to the sync psycopg2 driver — not
        installed here, and not what our async code needs anyway. This
        rewrites the scheme automatically so a plain string just works,
        regardless of how it arrives.
        """
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
