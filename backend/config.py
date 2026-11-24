"""Settings, read from the `.env` file in the project folder.

Rules that avoid the most common "my key does not work" problems:
  * `.env` WINS over any old value already set in the operating system.
  * quotes and spaces around a value are removed.
  * a placeholder such as `your-gemini-api-key-here` counts as "not set".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

_PLACEHOLDER_STARTS = ("your-", "your_", "<", "paste", "changeme", "xxx")
_PLACEHOLDER_ENDS = ("-here", "_here")


def clean_secret(value: str | None) -> str:
    """Strip spaces/quotes; return '' for empty values and obvious placeholders."""
    v = (value or "").strip().strip("'\"").strip()
    low = v.lower()
    if not v or low.startswith(_PLACEHOLDER_STARTS) or low.endswith(_PLACEHOLDER_ENDS):
        return ""
    return v


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, "").strip() or default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # secrets are excluded from repr so they can never leak into logs or error pages
    gemini_api_key: str = field(default="", repr=False)
    cloudflare_account_id: str = field(default="", repr=False)
    cloudflare_api_token: str = field(default="", repr=False)

    gemini_model: str = "gemini-3.8-flash"
    cloudflare_text_model: str = "@cf/meta/llama-3.1-8b-instruct"
    cloudflare_image_model: str = "@cf/black-forest-labs/flux-1-schnell"
    cloudflare_steps: int = 4                 # FLUX schnell: 1-8, 4 is the default
    image_fallback: bool = True               # draw an offline sketch if the AI image fails
    max_ai_images_per_day: int = 40           # safety cap (free allowance is ~170/day)
    text_timeout: int = 90
    image_timeout: int = 45
    generated_dir: Path = ROOT / "generated"
    # base URLs are settings only so tests can point at a local fake server
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    cloudflare_base_url: str = "https://api.cloudflare.com/client/v4"

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_cloudflare(self) -> bool:
        return bool(self.cloudflare_account_id and self.cloudflare_api_token)

    def summary(self) -> dict[str, str]:
        """Safe to print: shows only whether a secret is set, never its value."""
        return {
            "gemini_key": "set" if self.has_gemini else "missing",
            "cloudflare": "set" if self.has_cloudflare else "missing",
            "gemini_model": self.gemini_model,
            "image_model": self.cloudflare_image_model,
        }


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env", override=True)
    d = Settings()
    gen = os.getenv("GENERATED_DIR", "").strip()
    gen_dir = (ROOT / gen) if gen and not Path(gen).is_absolute() else Path(gen) if gen else d.generated_dir
    return Settings(
        gemini_api_key=clean_secret(os.getenv("GEMINI_API_KEY")),
        cloudflare_account_id=clean_secret(os.getenv("CLOUDFLARE_ACCOUNT_ID")),
        cloudflare_api_token=clean_secret(os.getenv("CLOUDFLARE_API_TOKEN")),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or d.gemini_model,
        cloudflare_text_model=os.getenv("CLOUDFLARE_TEXT_MODEL", "").strip() or d.cloudflare_text_model,
        cloudflare_image_model=os.getenv("CLOUDFLARE_IMAGE_MODEL", "").strip() or d.cloudflare_image_model,
        cloudflare_steps=_int("CLOUDFLARE_STEPS", d.cloudflare_steps, 1, 8),
        image_fallback=os.getenv("IMAGE_FALLBACK", "true").strip().lower() not in ("0", "false", "no", "off"),
        max_ai_images_per_day=_int("MAX_AI_IMAGES_PER_DAY", d.max_ai_images_per_day, 1, 500),
        text_timeout=_int("TEXT_TIMEOUT_SECONDS", d.text_timeout, 10, 300),
        image_timeout=_int("IMAGE_TIMEOUT_SECONDS", d.image_timeout, 10, 300),
        generated_dir=gen_dir,
    )
