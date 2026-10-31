"""Central configuration: model IDs, thresholds, paths. Agents never hard-code models."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

BACKEND_DIR = ROOT / "backend"
SKILLS_DIR = ROOT / "agent_skills"
RULES_PATH = ROOT / "config" / "rules.json"
REDLINE_TEMPLATES_PATH = ROOT / "config" / "redline_templates.json"
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
CONTRACTS_DIR = DATA_DIR / "contracts"
DB_PATH = DATA_DIR / "app.sqlite"
CHECKPOINT_DB_PATH = DATA_DIR / "checkpoints.sqlite"
SAMPLES_DIR = ROOT / "samples"
FONTS_DIR = BACKEND_DIR / "assets" / "fonts"

# Who reads the page images:
#   gemini: Google Gemini (cloud, GEMINI_API_KEY), one call per page; pages are sent to Google   [default]
#   historic: local Historic-Arabic-OCR (Qwen2-VL-2B + LoRA), line by line
#   qari:     local Qari-OCR v0.3 (Qwen2-VL-2B), whole page
#   claude:   Claude Opus vision (original design)
#   mock:     ground-truth files for the bundled samples only (tests, offline demo)
OCR_ENGINE = os.getenv("OCR_ENGINE", "gemini").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Embeddings of reviewed contracts for later search and export (local model, nothing leaves the machine).
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-base")
# Line segmentation: "kraken" (baseline model, follows tilted and curved lines; runs in its own
# environment, see kraken_seg.py) or "classic" (image analysis in page_layout.py, also the fallback).
SEGMENTER = os.getenv("SEGMENTER", "kraken").strip().lower()
KRAKEN_PYTHON = os.getenv("KRAKEN_PYTHON", str(ROOT / ".venv-kraken" / "bin" / "python"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")  # free tier (2.5 Flash-Lite is closed to new users)
# Free tier (a key without billing): no charge, but Google uses the content to improve its products.
# Set GEMINI_FREE_TIER=0 once billing is enabled so real prices count against GEMINI_BUDGET_USD.
GEMINI_FREE_TIER = os.getenv("GEMINI_FREE_TIER", "1").strip().lower() in {"1", "true", "yes"}
GEMINI_LABELS = {"gemini-2.5-flash-lite": "Gemini 2.5 Flash-Lite", "gemma-4-26b-a4b-it": "Gemma 4 26B", "gemma-4-31b-it": "Gemma 4 31B",
                 "gemini-flash-latest": "Gemini Flash (latest)", "gemini-2.5-pro": "Gemini 2.5 Pro", "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite", "gemini-3.6-flash": "Gemini 3.6 Flash",
                 "gemini-3.8-flash": "Gemini 3.8 Flash",
                 "gemini-4-argon": "Gemini 4 Argon"}
GEMINI_THINKING = os.getenv("GEMINI_THINKING", "medium")  # low | medium | high; dropped if the model rejects it
# Separate from the Claude cap: total Gemini spend across all contracts.
GEMINI_BUDGET_USD = float(os.getenv("GEMINI_BUDGET_USD", "2.0"))
# Open OCR models are not trained on Saudi court script; never let their confidence alone
# clear the auto-approve bar (min_mean_confidence_for_auto_approve is 0.92).
OCR_CONFIDENCE_CAP = float(os.getenv("OCR_CONFIDENCE_CAP", "0.9"))

def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes"}


# Spend controls. Economy mode (default) keeps Claude only where judgement is needed:
# redline wording and the reviewer summary come from templates, grouping and clause typing use Haiku.
# Super-resolution of small page photos before OCR (Real-ESRGAN). The OCR reads the enhanced page;
# the original stays the record.
ENHANCE_IMAGES = _flag("ENHANCE_IMAGES", True)
ENHANCE_MODEL = os.getenv("ENHANCE_MODEL", "x4plus")  # or "general-x4v3" (faster, softer)
# Only pages whose longest side is below this are enhanced (larger ones are read as they are).
ENHANCE_MIN_SIDE = int(os.getenv("ENHANCE_MIN_SIDE", "1600"))
# The 4x result is sized down to at most this longest side.
ENHANCE_TARGET_SIDE = int(os.getenv("ENHANCE_TARGET_SIDE", "2400"))

ECONOMY_MODE = _flag("ECONOMY_MODE", True)
EMBEDDINGS_ENABLED = _flag("EMBEDDINGS_ENABLED", True)
REDLINE_SOURCE = os.getenv("REDLINE_SOURCE", "template" if ECONOMY_MODE else "claude")
SUMMARY_SOURCE = os.getenv("SUMMARY_SOURCE", "template" if ECONOMY_MODE else "claude")
# Hard cap on total Claude spend across all contracts; past it every agent uses its rule-based fallback.
CLAUDE_BUDGET_USD = float(os.getenv("CLAUDE_BUDGET_USD", "4.5"))
# Below this mean OCR confidence the text is not trustworthy enough to review legally.
REVIEW_MIN_OCR_MEAN = float(os.getenv("REVIEW_MIN_OCR_MEAN", "0.4"))

_LIGHT = "claude-haiku-5-5" if ECONOMY_MODE else "claude-sonnet-5-5"

# Claude model IDs (confirmed against the claude-api skill, 2026-10).
MODELS = {
    "intake": os.getenv("MODEL_INTAKE", "claude-haiku-5-5"),
    "digitization": os.getenv("MODEL_DIGITIZATION", "claude-opus-5-5"),
    "structuring": os.getenv("MODEL_STRUCTURING", _LIGHT),
    "clause": os.getenv("MODEL_CLAUSE", _LIGHT),
    "deed": os.getenv("MODEL_DEED", "claude-haiku-5-5"),
    "policy": os.getenv("MODEL_POLICY", "claude-sonnet-5-5"),
    "redline": os.getenv("MODEL_REDLINE", "claude-sonnet-5-5"),
    "decision": os.getenv("MODEL_DECISION", "claude-haiku-5-5"),
}

# Used once if a model declines a request (stop_reason == "refusal").
FALLBACK_MODEL = os.getenv("MODEL_FALLBACK", "claude-sonnet-5-5")

# Effort per agent: vision transcription and policy judgement get more, triage less.
EFFORT = {
    "intake": "low",
    "digitization": "medium",
    "structuring": "low",
    "clause": "low",
    "deed": "low",
    "policy": "medium",
    "redline": "medium",
    "decision": "low",
}

MAX_TOKENS = {
    "intake": 2000,
    "digitization": 16000,
    "structuring": 12000,
    "clause": 8000,
    "deed": 12000,
    "policy": 12000,
    "redline": 16000,
    "decision": 2000,
}

# USD per million tokens: (input, output). Cache reads 0.1x, cache writes 1.25x input.
def price(model: str) -> tuple[float, float]:
    """USD per million (input, output) tokens. Gemini prices are paid-tier and change on set dates."""
    from datetime import date

    if model.startswith("gemma"):  # Gemma on the Gemini API: no charge
        return (0.0, 0.0)
    if model.startswith("gemini") and GEMINI_FREE_TIER:
        return (0.0, 0.0)
    if model == "gemini-4-argon":
        return (2.00, 10.00) if date.today() < date(2026, 12, 1) else (4.00, 20.00)
    if model == "gemini-3.5-flash-lite":
        return (0.30, 2.50)
    if model == "gemini-2.5-flash-lite":
        return (0.10, 0.40)
    if model == "gemini-2.5-pro":  # prompts up to 200k tokens
        return (1.25, 10.00)
    if model in ("gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"):  # same paid prices; the alias is the newest Flash
        return (0.75, 3.75) if date.today() < date(2027, 1, 1) else (1.50, 7.50)
    return PRICING.get(model, (0.0, 0.0))


PRICING = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

RENDER_DPI = 200
DIGITIZATION_CONCURRENCY = 4
INTAKE_MAX_PAGES = 3
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

PRODUCT_NAME_AR = "مُراجِع"


def mock_mode() -> bool:
    """Offline demo mode: deterministic stand-ins for Claude, used when no API key is set."""
    flag = os.getenv("MOCK_LLM", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    return not os.getenv("ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def thresholds() -> dict:
    return load_rules()["decision_thresholds"]


def ensure_dirs() -> None:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
