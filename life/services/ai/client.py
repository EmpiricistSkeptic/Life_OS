"""
life/services/ai/client.py

DeepSeek API client.
Handles the actual HTTP request, retries, and error handling.
"""

import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ── constants ─────────────────────────────────────────────────────────────────

DEEPSEEK_API_URL = settings.DEEPSEEK_API_URL
DEFAULT_MODEL    = settings.DEEPSEEK_MODEL
DEFAULT_TIMEOUT  = 60       # seconds
MAX_RETRIES      = 2
RETRY_DELAY      = 2        # seconds between retries


# ── exceptions ────────────────────────────────────────────────────────────────

class AIClientError(Exception):
    """Raised when DeepSeek API returns an error."""
    pass


class AITimeoutError(AIClientError):
    """Raised when request times out."""
    pass


class AIRateLimitError(AIClientError):
    """Raised when rate limit is hit (429)."""
    pass


# ── core client ───────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = getattr(settings, "DEEPSEEK_API_KEY", None)
    if not key:
        raise AIClientError(
            "DEEPSEEK_API_KEY is not set in Django settings."
        )
    return key


def _build_payload(
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "stream":      stream,
    }


def call_deepseek(
    system: str,
    user: str,
    model: str       = DEFAULT_MODEL,
    max_tokens: int  = 1500,
    temperature: float = 0.7,
    stream: bool     = False,
) -> str:
    """
    Send a (system, user) prompt pair to DeepSeek and return the response text.

    Args:
        system:      System prompt string
        user:        User message string
        model:       DeepSeek model name (default: deepseek-chat)
        max_tokens:  Max tokens in response
        temperature: Creativity 0.0–1.0 (0.7 is balanced)
        stream:      Streaming not supported yet, keep False

    Returns:
        Response text as string

    Raises:
        AIClientError:    API error or bad response
        AITimeoutError:   Request timed out
        AIRateLimitError: Rate limit hit
    """
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload = _build_payload(system, user, model, max_tokens, temperature, stream)

    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            logger.warning(f"[AI] Retry {attempt}/{MAX_RETRIES} after error: {last_error}")
            time.sleep(RETRY_DELAY * attempt)

        try:
            response = requests.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout:
            last_error = "Request timed out"
            continue
        except requests.ConnectionError as e:
            last_error = f"Connection error: {e}"
            continue

        # ── handle HTTP errors ────────────────────────────────────────────────
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", RETRY_DELAY * 2))
            logger.warning(f"[AI] Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            last_error = "Rate limit hit"
            continue

        if response.status_code == 401:
            raise AIClientError("Invalid API key. Check DEEPSEEK_API_KEY in settings.")

        if response.status_code == 402:
            raise AIClientError("Insufficient DeepSeek credits.")

        if not response.ok:
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            continue

        # ── parse response ────────────────────────────────────────────────────
        try:
            data = response.json()
        except json.JSONDecodeError:
            last_error = "Failed to parse API response as JSON"
            continue

        try:
            text = data["choices"][0]["message"]["content"]
            logger.info(
                f"[AI] Success — model={model}, "
                f"tokens={data.get('usage', {}).get('total_tokens', '?')}"
            )
            logger.info(
                "[AI] Response content length: %d",
                len(text) if text else 0,
            )

            logger.info(
                "[AI] Response preview: %r",
                text[:300] if text else text,
            )
            return text.strip()
        except (KeyError, IndexError) as e:
            last_error = f"Unexpected response structure: {e} — {data}"
            continue

    # all retries exhausted
    if "timed out" in str(last_error).lower():
        raise AITimeoutError(f"DeepSeek request timed out after {MAX_RETRIES + 1} attempts.")

    raise AIClientError(f"DeepSeek API failed after {MAX_RETRIES + 1} attempts. Last error: {last_error}")


# ── convenience wrapper ───────────────────────────────────────────────────────

def call_with_prompt(
    prompt_tuple: tuple[str, str],
    **kwargs: Any,
) -> str:
    """
    Convenience wrapper — accepts a (system, user) tuple directly
    as returned by any function in prompts.py.

    Usage:
        from life.services.ai.prompts import weekly_comparison_prompt
        from life.services.ai.client import call_with_prompt

        result = call_with_prompt(
            weekly_comparison_prompt(current, previous, "Dec 9-15", "Dec 2-8")
        )
    """
    system, user = prompt_tuple
    return call_deepseek(system, user, **kwargs)