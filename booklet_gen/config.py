import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    provider: str
    gemini_model_fast: str
    gemini_model_strong: str
    claude_model_fast: str
    claude_model_strong: str
    gemini_api_key: str
    anthropic_api_key: str
    max_retries: int


def load_config() -> Config:
    return Config(
        provider=os.environ.get("LLM_PROVIDER", "gemini").lower(),
        gemini_model_fast=os.environ.get("GEMINI_MODEL_FAST", "gemini-2.0-flash"),
        gemini_model_strong=os.environ.get("GEMINI_MODEL_STRONG", "gemini-2.5-pro"),
        claude_model_fast=os.environ.get("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"),
        claude_model_strong=os.environ.get("CLAUDE_MODEL_STRONG", "claude-opus-4-8"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        max_retries=int(os.environ.get("MAX_RETRIES", "3")),
    )
