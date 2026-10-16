"""ai_analyzer's settings, read from the environment and .env (OpenAI, Redis, main app, ToolSvc, our DB)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Shares the repo's .env for now; give it its own file when it becomes a separate service.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    # The scope gate (agent/scope.py): a small model that decides if a message needs the agent at all
    scope_model: str = "gpt-5.4-nano"
    redis_url: str = "redis://localhost:6379/0"
    # The main app serves message content at /internal/...
    main_app_url: str = "http://localhost:8000"
    # ToolSvc runs the support tools the LLM asks for (POST /tools/<name>)
    toolsvc_url: str = "http://localhost:8002"
    # C1 appends every agent run's report (SOPs, tools, timing, tokens, summary, reply) to this file
    agent_runs_file: str = "agent_runs.log"
    # Our own database (agent_runs, reply_reviews).
    # Not DATABASE_URL: that is the main app's, in the same .env.
    analyzer_database_url: str = "postgresql+psycopg://localhost:5432/ai_analyzer"
    # C1 serves Prometheus metrics on http://localhost:<port>/metrics (scraped by monitoring/prometheus.yml)
    analyzer_metrics_port: int = 9101


@lru_cache
def get_settings() -> Settings:
    return Settings()
