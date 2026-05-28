"""
Settings loaded once from .env via Pydantic Settings.
Access anywhere with: from agent.config import settings
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── LLM ──────────────────────────────────────────────
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    claude_model: str = Field("claude-sonnet-4-5", alias="CLAUDE_MODEL")

    # ── Jira ─────────────────────────────────────────────
    jira_base_url: str = Field(..., alias="JIRA_BASE_URL")
    jira_user_email: str = Field(..., alias="JIRA_USER_EMAIL")
    jira_api_token: str = Field(..., alias="JIRA_API_TOKEN")
    jira_project_key: str = Field("KAN", alias="JIRA_PROJECT_KEY")

    # ── ChromaDB ─────────────────────────────────────────
    chroma_persist_dir: str = Field("./chroma_db", alias="CHROMA_PERSIST_DIR")
    chroma_collection: str = Field("helix_policies", alias="CHROMA_COLLECTION")

    # ── Agent tuning ─────────────────────────────────────
    confidence_threshold: float = Field(0.45, alias="CONFIDENCE_THRESHOLD")
    max_chunks: int = Field(4, alias="MAX_CHUNKS")
    # temperature=0 for deterministic, consistent JSON output across all LLM calls
    llm_temperature: float = Field(0.0, alias="LLM_TEMPERATURE")

    # ── FastAPI ───────────────────────────────────────────
    webhook_secret: str = Field("", alias="WEBHOOK_SECRET")
    api_host: str = Field("0.0.0.0", alias="API_HOST")
    api_port: int = Field(8000, alias="API_PORT")


settings = Settings()
