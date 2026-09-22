from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
