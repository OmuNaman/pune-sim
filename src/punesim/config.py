"""Env-driven configuration (.env, gitignored; see .env.example).

OpenRouter is the single door to every model — one key, any provider. Model
slugs are pure config: verify current slugs at openrouter.ai/models and pin
them in .env. SCENE_GATE_MODE is the owner's LLM-for-everyone dial:
"spotlight" (attention top-k households fire scenes) or "all" (every household
fires — same machinery, linear cost).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Config:
    run_seed: int
    llm_mode: str  # live | record | replay
    scene_gate_mode: str  # spotlight | all
    openrouter_api_key: str | None
    openrouter_base_url: str
    model_workhorse: str  # scene prose (T1/T2)
    model_flash: str  # structure-only: micro, qc, digests
    model_premium: str  # T3 focal + identity-salient (tier >= 1) scenes
    runs_dir: Path

    @property
    def cassette_path(self) -> Path:
        return self.runs_dir / "cassettes.db"


def from_env(env_file: str | Path | None = None) -> Config:
    load_dotenv(env_file or ".env")
    runs_dir = Path(os.getenv("PUNESIM_RUNS_DIR", "runs"))
    runs_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        run_seed=int(os.getenv("PUNESIM_RUN_SEED", "108")),
        llm_mode=os.getenv("PUNESIM_LLM", "record"),
        scene_gate_mode=os.getenv("SCENE_GATE_MODE", "spotlight"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", _DEFAULT_BASE_URL),
        model_workhorse=os.getenv("PUNESIM_MODEL_WORKHORSE", "deepseek/deepseek-chat"),
        model_flash=os.getenv("PUNESIM_MODEL_FLASH", "deepseek/deepseek-chat"),
        model_premium=os.getenv("PUNESIM_MODEL_PREMIUM", "anthropic/claude-sonnet-4.5"),
        runs_dir=runs_dir,
    )
