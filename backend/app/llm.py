"""Claude client, skill loader, and the structured-output helper every agent uses."""
from __future__ import annotations

import base64
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from . import config, db

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)

_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


def budget_left() -> float:
    return config.CLAUDE_BUDGET_USD - db.total_spend(exclude="gemini")


@lru_cache(maxsize=None)
def load_skill(name: str) -> str:
    """agent_skills/<name>/SKILL.md without YAML frontmatter, used as the system prompt."""
    text = (config.SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    return _FRONTMATTER.sub("", text, count=1).strip()


@lru_cache(maxsize=1)
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=3, timeout=300)


def image_block(path: str | Path) -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def text_block(text: str, cache: bool = False) -> dict:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def rules_block() -> dict:
    """rules.json repeats on every Policy and Redline call, so it is a cached prefix block."""
    rules = json.dumps(config.load_rules(), ensure_ascii=False, sort_keys=True, indent=1)
    return text_block(f"<rules_json>\n{rules}\n</rules_json>", cache=True)


def _record_usage(contract_id: str | None, agent: str, model: str, usage: Any) -> None:
    if not contract_id or usage is None:
        return
    db.add_usage(
        contract_id, agent, model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def call_structured(
    skill: str,
    agent: str,
    content_blocks: list[dict],
    output_model: type[T],
    *,
    contract_id: str | None = None,
    mock: Callable[[], T | dict] | None = None,
) -> T:
    """System prompt + user content, JSON forced to output_model's schema, validated with Pydantic.

    One retry with the validation error appended, a model fallback on refusal, then raise.
    In mock mode the `mock` callable supplies the answer and no API call is made.
    """
    over_budget = not config.mock_mode() and budget_left() <= 0
    if config.mock_mode() or over_budget:
        if over_budget:
            log.warning("agent=%s Claude budget of $%.2f used up; rule-based fallback", agent, config.CLAUDE_BUDGET_USD)
            if contract_id:
                from .events import emit
                emit(contract_id, agent, "info", "نفدت ميزانية Claude المحددة، استُخدمت القواعد البديلة")
        if mock is None:
            raise (BudgetExceeded if over_budget else LLMError)(f"no fallback available for {agent}")
        result = mock()
        return result if isinstance(result, output_model) else output_model.model_validate(result)

    model = config.MODELS[agent]
    system = [text_block(load_skill(skill), cache=True)]
    messages: list[dict] = [{"role": "user", "content": content_blocks}]
    last_error: str | None = None
    tried_fallback = False

    for attempt in range(3):
        try:
            response = client().messages.parse(
                model=model,
                max_tokens=config.MAX_TOKENS[agent],
                system=system,
                messages=messages,
                output_format=output_model,
                output_config={"effort": config.EFFORT[agent]},
            )
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)[:1500]
            log.warning("agent=%s attempt=%d invalid structured output", agent, attempt)
            messages = [{"role": "user", "content": content_blocks + [text_block(
                "The previous answer failed schema validation with this error. Return corrected JSON only.\n"
                + last_error)]}]
            continue

        _record_usage(contract_id, agent, model, response.usage)

        if response.stop_reason == "refusal":
            if tried_fallback:
                raise LLMError(f"{agent}: request declined by both models")
            log.warning("agent=%s refusal on %s, retrying on %s", agent, model, config.FALLBACK_MODEL)
            model, tried_fallback = config.FALLBACK_MODEL, True
            continue
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{agent}: output truncated at max_tokens")

        parsed = response.parsed_output
        if parsed is not None:
            return parsed
        last_error = "empty structured output"
    raise LLMError(f"{agent}: could not get valid output ({last_error})")
