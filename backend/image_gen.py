"""Illustrations: Cloudflare FLUX.1 schnell (AI), with an optional offline sketch as a safety net.

One book = one image per page (the cover reuses page 1's picture), so a 5-page story costs 5 AI images,
roughly 58 Neurons each, against Cloudflare's free 10,000 Neurons per day.
"""
from __future__ import annotations

import base64
import binascii
import io
from typing import Callable, Optional

from PIL import Image, UnidentifiedImageError

from .config import Settings
from .errors import ConfigError, ProviderError, StorybookError
from .models import Page, Story
from .net import post_json
from .prompts import image_prompt
from .sketch import draw_sketch
from .storage import add_ai_image, ai_images_today, images_dir, story_dir

ProgressFn = Callable[[int, int, str], None]
MAX_SIDE = 1280
FATAL_STATUS = {401, 403, 404, 429}  # retrying other pages would fail the same way: stop early


def decode_image(data: bytes) -> bytes:
    """Check the bytes are a real picture and return a clean PNG (so a bad reply never reaches the PDF)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.width < 64 or img.height < 64:
                raise ProviderError("The image model returned a tiny or empty picture.")
            img = img.convert("RGB")
            if max(img.size) > MAX_SIDE:
                img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ProviderError("The image model returned data that is not a valid picture.") from None


class ImageMaker:
    def __init__(self, settings: Settings):
        self.s = settings

    # --- status for the UI ------------------------------------------------
    def is_configured(self) -> bool:
        return self.s.has_cloudflare

    @property
    def provider_label(self) -> str:
        return self.s.cloudflare_image_model.split("/")[-1] + " (Cloudflare)"

    def setup_message(self) -> str:
        suffix = " Until then pages get a simple offline sketch." if self.s.image_fallback else ""
        return ("AI illustrations need CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN in the .env file "
                "(free account, see README.md). Then restart the app." + suffix)

    # --- the Cloudflare call ----------------------------------------------
    def _ai_image(self, prompt: str, seed: int) -> bytes:
        s = self.s
        if not s.has_cloudflare:
            raise ConfigError(self.setup_message())
        used = ai_images_today(s.generated_dir)
        if used >= s.max_ai_images_per_day:
            raise ProviderError(f"Safety cap reached: {used} AI images today (MAX_AI_IMAGES_PER_DAY="
                                f"{s.max_ai_images_per_day}). Raise it in .env if you really want more.")
        response = post_json(
            f"{s.cloudflare_base_url}/accounts/{s.cloudflare_account_id}/ai/run/{s.cloudflare_image_model}",
            headers={"Authorization": f"Bearer {s.cloudflare_api_token}"},
            payload={"prompt": prompt, "steps": s.cloudflare_steps},  # this model rejects a "seed" field (HTTP 400, code 5006)
            timeout=s.image_timeout, provider="Cloudflare (image)", key_name="CLOUDFLARE_API_TOKEN",
            secrets=[s.cloudflare_api_token, s.cloudflare_account_id],
        )
        if response.headers.get("content-type", "").startswith("image/"):
            raw = response.content
        else:
            try:
                data = response.json()
            except ValueError:
                raise ProviderError("Cloudflare returned something that is not JSON or an image.") from None
            if isinstance(data, dict) and data.get("success") is False:
                errors = data.get("errors") or [{}]
                raise ProviderError("Cloudflare reported an error: "
                                    f"{errors[0].get('message', 'unknown') if isinstance(errors[0], dict) else errors[0]}")
            result = data.get("result") if isinstance(data, dict) else None
            encoded = (result.get("image") if isinstance(result, dict) else result) or \
                      (data.get("image") if isinstance(data, dict) else None)
            if not isinstance(encoded, str) or not encoded:
                raise ProviderError("Cloudflare's answer contained no image.")
            try:
                raw = base64.b64decode(encoded.split(",", 1)[-1], validate=False)
            except (binascii.Error, ValueError):
                raise ProviderError("Cloudflare's image data could not be decoded.") from None
        png = decode_image(raw)
        add_ai_image(s.generated_dir)
        return png

    # --- saving -------------------------------------------------------------
    def _store(self, story: Story, page: Page, png: bytes, *, is_ai: bool, error: Optional[str]) -> None:
        version = page.image_version + 1
        name = f"page_{page.page_number:02d}_v{version}.png"
        (images_dir(self.s.generated_dir, story) / name).write_bytes(png)
        page.image_path = f"images/{name}"
        page.image_version = version
        page.image_is_ai = is_ai
        page.image_model = self.provider_label if is_ai else "offline sketch (not AI)"
        page.image_error = error

    # --- public API -----------------------------------------------------------
    def _generate(self, story: Story, page: Page, user_note: str, seed_offset: int) -> tuple[bool, Optional[int]]:
        """Returns (is_ai_image, http_status_of_the_failure_or_None)."""
        prompt = image_prompt(story.character, story.input.style, page, user_note)
        try:
            png = self._ai_image(prompt, (story.image_seed + seed_offset * 7919) % (2**31 - 1))
        except StorybookError as exc:
            status = getattr(exc, "status", None)
            if page.image_path and page.image_is_ai:  # keep the good picture we already have
                page.image_error = f"Could not regenerate; kept the previous picture. {exc}"
                return True, status
            if not self.s.image_fallback:
                raise
            self._store(story, page, draw_sketch(story.character, page), is_ai=False, error=str(exc))
            return False, status if status is not None else (0 if isinstance(exc, ConfigError) else None)
        self._store(story, page, png, is_ai=True, error=None)
        return True, None

    def generate_page(self, story: Story, page: Page, user_note: str = "", seed_offset: int = 0) -> bool:
        """Illustrate one page. Returns True if it is now an AI image.

        If the AI call fails: an existing AI picture is kept (and the error shown); a page with no AI
        picture gets the offline sketch (when IMAGE_FALLBACK is on) plus the reason. Without the fallback
        the error is raised.
        """
        return self._generate(story, page, user_note, seed_offset)[0]

    def generate_all(self, story: Story, progress: Optional[ProgressFn] = None,
                     only_missing: bool = True, on_page: Optional[Callable[[Story], None]] = None) -> list[int]:
        """Illustrate every page that still lacks an AI picture. Returns the page numbers that ended up
        without one. If the account is out of quota or the key is rejected, the remaining pages skip the
        doomed call and get the sketch straight away."""
        pages = story.pages_without_ai_image() if only_missing else list(story.pages)
        failed: list[int] = []
        stop_reason: Optional[str] = None
        in_a_row = 0  # consecutive AI failures; after 2 we stop calling (network down, quota, bad key...)
        for done, page in enumerate(pages):
            if progress:
                progress(done, len(pages), f"Illustrating page {page.page_number} of {len(story.pages)}...")
            if stop_reason is not None:
                if self.s.image_fallback:
                    self._store(story, page, draw_sketch(story.character, page), is_ai=False, error=stop_reason)
                failed.append(page.page_number)
                if on_page:
                    on_page(story)
                continue
            try:
                ok, status = self._generate(story, page, "", 0)
            except StorybookError as exc:  # only when IMAGE_FALLBACK is off
                page.image_error = str(exc)
                ok, status = False, getattr(exc, "status", None)
                if isinstance(exc, ConfigError):
                    status = 0
            if not ok and (status in FATAL_STATUS or status == 0):
                stop_reason = page.image_error
            in_a_row = 0 if ok else in_a_row + 1
            if not ok and in_a_row >= 2 and stop_reason is None:
                stop_reason = page.image_error
            if not ok:
                failed.append(page.page_number)
            if on_page:
                on_page(story)  # save after every page so a closed tab loses nothing
        if progress:
            progress(len(pages), len(pages), "Done")
        return failed
