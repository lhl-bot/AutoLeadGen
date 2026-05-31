"""Shared LLM client — single entry point for all AI API calls."""
import os
import json
import time
import logging
import requests
from typing import Optional, Dict, Any, List
from services.http_client import http as _http

logger = logging.getLogger("llm_client")

LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("MINIMAX_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed")


def _llm_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def _parse_llm_response(data: dict) -> str:
    """Extract text content from LLM response, stripping 思考 tags."""
    choices = data.get("choices", [])
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content", "")
    # Strip  think/思考 blocks
    import re
    content = re.sub(r"<[^>]*think[^>]*>.*?</[^>]*think[^>]*>", "", content, flags=re.DOTALL)
    content = re.sub(r"<[^>]*思考[^>]*>.*?</[^>]*思考[^>]*>", "", content, flags=re.DOTALL)
    return content.strip()


def call_llm(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 120,
    max_retries: int = 1,
    response_format: Optional[str] = None,
) -> str:
    """Send a chat completion request to the configured LLM.

    Args:
        messages: List of {"role": "...", "content": "..."} dicts
        model: Override default LLM model
        temperature: Sampling temperature
        max_tokens: Max tokens in response
        timeout: HTTP timeout in seconds
        max_retries: Number of retries on failure
        response_format: Optional "json_object" for structured output

    Returns:
        Text content of the LLM response, or "" on failure
    """
    if not LLM_API_KEY:
        logger.error("LLM_API_KEY not configured")
        return ""

    payload: Dict[str, Any] = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = {"type": response_format}

    last_error = ""
    for attempt in range(max_retries):
        try:
            resp = _http.post(LLM_BASE_URL, headers=_llm_headers(), json=payload, timeout=timeout)
            if resp.status_code == 429:
                wait = min((attempt + 1) * 2, 10)
                logger.warning(f"LLM rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                logger.error(f"LLM call failed: {last_error} — {resp.text[:200]}")
                continue
            data = resp.json()
            result = _parse_llm_response(data)
            if not result and attempt + 1 < max_retries:
                logger.warning("LLM returned empty response, retrying...")
                continue
            return result
        except requests.Timeout:
            last_error = "timeout"
            logger.warning(f"LLM call timed out after {timeout}s")
        except Exception as e:
            last_error = str(e)
            logger.error(f"LLM call exception: {e}")
    return ""


def call_llm_json(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.3,
    timeout: int = 60,
    max_retries: int = 2,
) -> Optional[dict]:
    """Call LLM and parse response as JSON. Returns None on parse failure."""
    text = call_llm(
        messages,
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        response_format="json_object",
    )
    if not text:
        return None
    # Strip markdown code fences if present
    import re
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM JSON response: {text[:200]}")
        return None
