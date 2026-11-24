"""One HTTP helper for every web API call, so retry and error handling exist in exactly one place.

* Retries only temporary problems (HTTP 408/429/5xx, timeouts, dropped connections), 2 times, with a short wait.
* Turns every failure into a `ProviderError` with a message a user can act on.
* Secrets are removed from any message before it is raised.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

from .errors import ProviderError

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _body_message(response: requests.Response) -> str:
    """Pull the human-readable error text out of a Google or Cloudflare error body."""
    try:
        data = response.json()
    except ValueError:
        return " ".join(response.text.split())[:300]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):  # Google: {"error": {"message": ...}}
            return str(err.get("message", ""))[:300]
        errors = data.get("errors")
        if isinstance(errors, list) and errors:  # Cloudflare: {"errors": [{"code":..,"message":..}]}
            first = errors[0]
            if isinstance(first, dict):
                return f"{first.get('message', '')} (code {first.get('code', '?')})"[:300]
    return " ".join(str(data).split())[:300]


def explain(provider: str, status: int, detail: str, hint_key_name: str) -> str:
    """Friendly, specific message for an HTTP error."""
    low = detail.lower()
    if "api key not valid" in low or "api_key_invalid" in low:
        return (f"{provider} says the API key is not valid. Open the .env file, check {hint_key_name} "
                "(the real key, no spaces, no placeholder), save it, then STOP and RESTART Streamlit.")
    if status in (401, 403):
        return (f"{provider} rejected the credentials (HTTP {status}): {detail or 'no details'}. "
                f"Check {hint_key_name} in .env; Cloudflare tokens need the 'Workers AI' permission and "
                "the Account ID must belong to the same account.")
    if status == 404:
        return (f"{provider} could not find the model or account (HTTP 404): {detail or 'no details'}. "
                "Check the Account ID and the model name in .env.")
    if status == 429:
        return (f"{provider} says the rate limit or daily free allowance is used up (HTTP 429). "
                "Cloudflare's free allowance resets at 00:00 UTC (05:30 AM India time); wait a minute and retry.")
    if status == 402:
        return f"{provider} says payment or credits are required (HTTP 402)."
    if status >= 500:
        return f"{provider} is temporarily unavailable (HTTP {status}). Try again in a minute."
    hint = ""
    if "nsfw" in low or "safety" in low or "flagged" in low:
        hint = " The provider's safety filter blocked it; make the scene description simpler and retry."
    return f"{provider} rejected the request (HTTP {status}): {detail or 'no details'}.{hint}"


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    provider: str,
    key_name: str,
    secrets: list[str],
    retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> requests.Response:
    """POST a JSON body and return the successful response, or raise ProviderError."""
    attempt = 0
    while True:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                (sleep or time.sleep)(1.5 * (2 ** attempt))
                attempt += 1
                continue
            what = "timed out" if isinstance(exc, requests.Timeout) else "could not be reached"
            raise ProviderError(f"{provider} {what}. Check your internet connection and try again.") from None
        except requests.RequestException as exc:
            raise ProviderError(_redact(f"{provider} request failed: {exc}", secrets)) from None

        if response.status_code < 400:
            return response
        if response.status_code in RETRY_STATUS and attempt < retries:
            try:
                wait = min(float(response.headers.get("Retry-After", "")), 10.0)
            except ValueError:
                wait = 1.5 * (2 ** attempt)
            (sleep or time.sleep)(wait)
            attempt += 1
            continue
        detail = _redact(_body_message(response), secrets)
        raise ProviderError(explain(provider, response.status_code, detail, key_name), response.status_code)
