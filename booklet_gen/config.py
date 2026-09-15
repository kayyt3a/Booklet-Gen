import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    provider: str
    gemini_model_fast: str
    gemini_model_strong: str
    gemini_model_exact: str
    claude_model_fast: str
    claude_model_strong: str
    claude_model_exact: str
    gemini_api_key: str
    anthropic_api_key: str
    max_retries: int


def load_config() -> Config:
    # The "exact" tier is for work where being right matters more than being
    # quick: today that is the answer validator alone. It falls back to the
    # strong model, so leaving it unset changes nothing, and setting it buys a
    # better grader without making every booklet slower. Upgrading the strong
    # tier upgrades six agents and three sequential steps per subtopic;
    # upgrading this one upgrades the step whose output a parent actually
    # trusts, and it is the batched call, so one per subtopic rather than one
    # per question.
    gemini_strong = os.environ.get("GEMINI_MODEL_STRONG", "gemini-2.5-flash")
    claude_strong = os.environ.get("CLAUDE_MODEL_STRONG", "claude-opus-4-8")
    return Config(
        provider=os.environ.get("LLM_PROVIDER", "gemini").lower(),
        gemini_model_fast=os.environ.get("GEMINI_MODEL_FAST", "gemini-2.5-flash"),
        gemini_model_strong=gemini_strong,
        gemini_model_exact=os.environ.get("GEMINI_MODEL_EXACT", gemini_strong),
        claude_model_fast=os.environ.get("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"),
        claude_model_strong=claude_strong,
        claude_model_exact=os.environ.get("CLAUDE_MODEL_EXACT", claude_strong),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        max_retries=int(os.environ.get("MAX_RETRIES", "3")),
    )
