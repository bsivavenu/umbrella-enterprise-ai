"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket: str = ""

    # Uploads
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB

    # Embeddings: vector size, must match the Pinecone index
    embed_dim: int = 1024

    # OpenAI (embeddings) and Anthropic (chat)
    openai_api_key: str = ""
    openai_embed_model: str = "text-embedding-3-large"  # output size set by embed_dim
    anthropic_api_key: str = ""
    anthropic_chat_model: str = "claude-haiku-5-5"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = ""
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_ready_timeout: int = 180

    # Postgres
    database_url: str = ""

    # Access: optional shared password for the whole app (HTTP Basic); empty = no password
    app_password: str = ""

    # RAG behaviour
    min_relevance: float = 0.30   # drop search hits scoring below this (cosine, 0-1); measured: unrelated <= 0.23, real matches >= 0.33
    memory_turns: int = 6         # past question/answer pairs sent back to the model
    hitl_enabled: bool = True     # human-in-the-loop approvals (used later by main.py)

    # Observability: pick the tool and how much to record.
    #   provider: rius | langsmith | none
    #   capture:  off (timing + errors only) | metadata (+ token counts, model) | full (+ prompts and answers)
    observability_provider: Literal["rius", "langsmith", "none"] = "langsmith"
    observability_capture: Literal["off", "metadata", "full"] = "metadata"

    # LangSmith (traces go to the project below; it is created automatically on first trace)
    langsmith_api_key: str = ""
    langsmith_project: str = "umbrella"

    # Rius
    rius_api_key: str = ""
    rius_service_name: str = "umbrella_ai"
    rius_disabled: bool = False

    @field_validator("observability_provider", "observability_capture", mode="before")
    @classmethod
    def _normalise(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


@lru_cache
def settings() -> Settings:
    """Cached settings instance: `from app.config import settings; settings().s3_bucket`."""
    return Settings()


get_settings = settings  # alias
