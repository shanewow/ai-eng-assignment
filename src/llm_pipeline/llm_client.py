"""Thin chat-completions wrapper with an on-disk response cache.

Why a cache: extraction is deterministic per (model, prompt, parameters) for
our purposes, and the eval harness re-runs the same prompts many times.
Keying responses by a content hash makes re-runs free and reproducible, and
it is the same discipline the production design needs (docs/ASSESSMENT.md 8.1):
never extract the same review twice.

Why parameter translation: the gpt-5 family are reasoning models. They take
`max_completion_tokens` instead of `max_tokens`, reject a non-default
temperature, and accept `reasoning_effort`. Callers pass one set of arguments
and this module maps them to whichever family the model belongs to.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_CACHE_DIR = "data/cache/llm"
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


@dataclass
class ChatResult:
    content: str
    model: str
    cached: bool
    usage: dict = field(default_factory=dict)
    request_hash: str = ""


class CachedChatClient:
    """OpenAI chat completions with a JSON file cache keyed by request hash."""

    def __init__(
        self,
        cache_dir: str | os.PathLike = DEFAULT_CACHE_DIR,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        use_cache: bool = True,
    ):
        self.cache_dir = Path(cache_dir)
        self.use_cache = use_cache
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = None  # created on first uncached call

    # -- OpenAI client ---------------------------------------------------------

    def _openai(self):
        if self._client is None:
            from openai import OpenAI

            key = self._api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set and the response is not cached"
                )
            self._client = OpenAI(
                api_key=key, timeout=self._timeout, max_retries=self._max_retries
            )
        return self._client

    # -- Parameter mapping -----------------------------------------------------

    @staticmethod
    def build_params(
        model: str,
        *,
        temperature: Optional[float],
        max_tokens: Optional[int],
        reasoning_effort: Optional[str],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if is_reasoning_model(model):
            if max_tokens is not None:
                params["max_completion_tokens"] = max_tokens
            params["reasoning_effort"] = reasoning_effort or "minimal"
        else:
            if temperature is not None:
                params["temperature"] = temperature
            if max_tokens is not None:
                params["max_tokens"] = max_tokens
        return params

    # -- Cache -----------------------------------------------------------------

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # -- Public API ------------------------------------------------------------

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: Optional[dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> ChatResult:
        params = self.build_params(
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        payload = {
            "model": model,
            "messages": messages,
            "response_format": response_format,
            "params": params,
        }
        key = self.request_hash(payload)
        path = self._cache_path(key)

        if self.use_cache and path.exists():
            entry = json.loads(path.read_text(encoding="utf-8"))
            return ChatResult(
                content=entry["response"]["content"],
                model=entry["response"].get("model", model),
                cached=True,
                usage=entry["response"].get("usage", {}),
                request_hash=key,
            )

        kwargs: dict[str, Any] = {"model": model, "messages": messages, **params}
        if response_format is not None:
            kwargs["response_format"] = response_format

        logger.debug(f"LLM call model={model} hash={key[:12]}")
        response = self._openai().chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        usage = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached_tokens = getattr(details, "cached_tokens", None) if details else None
            if cached_tokens:
                usage["cached_prompt_tokens"] = cached_tokens

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "request": payload,
                "response": {
                    "content": content,
                    "model": response.model,
                    "usage": usage,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            path.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        return ChatResult(
            content=content,
            model=response.model,
            cached=False,
            usage=usage,
            request_hash=key,
        )
