from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./skill2hire.db"
    jwt_secret: str = "dev-only-change-this-secret"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    frontend_origin: str = "http://127.0.0.1:5173"
    sendgrid_api_key: str | None = None
    mail_from: str = "noreply@skill2hire.local"
    google_ai_api_key: str | None = None
    openai_api_key: str | None = None
    assistant_model: str = "gemini-2.0-flash"
    google_client_id: str | None = None
    linkedin_client_id: str | None = None
    azure_client_id: str | None = None
    oauth_redirect_uri: str = "http://127.0.0.1:8000/auth/oauth/callback"
    redis_url: str = "redis://localhost:6379/0"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
