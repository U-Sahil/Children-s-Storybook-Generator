"""Story writer.

Order of attempts (the first one that works wins):
  1. Google Gemini  (needs GEMINI_API_KEY; free tier exists)
  2. Cloudflare Llama (uses the SAME Cloudflare account + token as the images, so no second key)

Both are plain HTTPS calls made with `requests`; no vendor SDK is needed.
"""
from __future__ import annotations

import json
from typing import Callable

from pydantic import ValidationError

from .config import Settings
from .errors import ConfigError, ProviderError, StoryError, StorybookError
from .models import Character, Companion, LLMCharacter, LLMStory, Page, Story, StoryInput
from .net import post_json
from .prompts import STORY_SYSTEM, repair_prompt, story_prompt


# ----------------------------------------------------------------------------
# Parsing: the model's text -> a validated LLMStory
# ----------------------------------------------------------------------------
def extract_json(text: str) -> dict:
    """Find the JSON object inside a reply that may contain ```fences``` or chatter."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise StoryError("The model did not return JSON.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise StoryError(f"The model returned broken JSON ({exc.__class__.__name__}).") from None
    if not isinstance(data, dict):
        raise StoryError("The model returned JSON, but not an object.")
    return data


def parse_story(text: str, expected_pages: int) -> LLMStory:
    data = extract_json(text)
    try:
        story = LLMStory.model_validate(data)
    except ValidationError as exc:
        missing = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")}))
        raise StoryError(f"The JSON is missing or has wrong fields: {missing or 'structure'}.") from None
    if len(story.pages) != expected_pages:
        raise StoryError(f"Expected exactly {expected_pages} pages but got {len(story.pages)}.")
    for i, page in enumerate(story.pages, 1):
        if not page.text.strip() or not page.scene_description.strip():
            raise StoryError(f"Page {i} has empty text or scene_description.")
    if not story.title.strip() or not story.moral.strip():
        raise StoryError("The story has no title or no moral.")
    return story


def build_character(llm: LLMCharacter, inp: StoryInput) -> Character:
    """Name, age come from the user (authoritative); looks come from the LLM, with safe defaults."""
    def clean(v: str, fallback: str) -> str:
        return " ".join((v or "").split()) or fallback

    return Character(
        name=inp.child_name, age=inp.age,
        hair=clean(llm.hair, "short brown hair"), face=clean(llm.face, "friendly smiling face"),
        clothing=clean(llm.clothing, "colorful everyday clothes"), colors=clean(llm.colors, "bright primary colors"),
        distinctive_features=clean(llm.distinctive_features, "a curious expression"),
        companions=[Companion(name=c.name.strip(), description=" ".join(c.description.split()))
                    for c in llm.companions if c.name.strip() and c.description.strip()][:2],
    )


# ----------------------------------------------------------------------------
# The writer
# ----------------------------------------------------------------------------
class StoryWriter:
    def __init__(self, settings: Settings):
        self.s = settings
        self._gemini_model = settings.gemini_model

    # --- status used by the UI ------------------------------------------
    def is_configured(self) -> bool:
        return self.s.has_gemini or self.s.has_cloudflare

    @property
    def label(self) -> str:
        if self.s.has_gemini:
            return self._gemini_model
        return self.s.cloudflare_text_model

    def setup_message(self) -> str:
        return ("No story model is configured. Put GEMINI_API_KEY (free at aistudio.google.com) in the .env file, "
                "or CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN. Then restart the app. See README.md.")

    # --- the two providers: each takes (system, user) and returns the reply text ----
    def _call_gemini(self, system: str, user: str) -> str:
        def once(model: str):
            return post_json(
                f"{self.s.gemini_base_url}/models/{model}:generateContent",
                headers={"x-goog-api-key": self.s.gemini_api_key, "Content-Type": "application/json"},
                payload={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
                },
                timeout=self.s.text_timeout, provider="Gemini", key_name="GEMINI_API_KEY",
                secrets=[self.s.gemini_api_key],
            )

        try:
            response = once(self._gemini_model)
        except ProviderError as exc:
            if exc.status != 404:
                raise
            # The model name was retired or renamed: look up a current Flash model and retry once.
            found = self._discover_gemini_model()
            if not found or found == self._gemini_model:
                raise
            self._gemini_model = found
            response = once(found)
        try:
            data = response.json()
            candidate = data["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (ValueError, KeyError, IndexError, TypeError):
            block = ""
            try:
                block = response.json().get("promptFeedback", {}).get("blockReason", "")
            except ValueError:
                pass
            raise ProviderError("Gemini returned no story text" + (f" (blocked: {block})." if block else ".")) from None
        if not text.strip():
            raise ProviderError("Gemini returned an empty answer.")
        return text

    def _discover_gemini_model(self) -> str | None:
        import requests
        try:
            r = requests.get(f"{self.s.gemini_base_url}/models", params={"pageSize": 200},
                             headers={"x-goog-api-key": self.s.gemini_api_key}, timeout=self.s.text_timeout)
            models = r.json().get("models", []) if r.status_code == 200 else []
        except (requests.RequestException, ValueError):
            return None
        names = []
        for m in models:
            name = str(m.get("name", "")).removeprefix("models/")
            methods = m.get("supportedGenerationMethods", [])
            if (name.startswith("gemini") and "flash" in name and "generateContent" in methods
                    and not any(bad in name for bad in ("image", "tts", "live", "audio", "lite", "robotics"))):
                names.append(name)
        return sorted(names, reverse=True)[0] if names else None

    def _call_cloudflare(self, system: str, user: str) -> str:
        s = self.s
        response = post_json(
            f"{s.cloudflare_base_url}/accounts/{s.cloudflare_account_id}/ai/run/{s.cloudflare_text_model}",
            headers={"Authorization": f"Bearer {s.cloudflare_api_token}"},
            payload={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                     "max_tokens": 2500, "temperature": 0.8},
            timeout=s.text_timeout, provider="Cloudflare (story)", key_name="CLOUDFLARE_API_TOKEN",
            secrets=[s.cloudflare_api_token, s.cloudflare_account_id],
        )
        try:
            data = response.json()
            result = data["result"]["response"]
        except (ValueError, KeyError, TypeError):
            raise ProviderError("Cloudflare returned an unexpected answer for the story.") from None
        if data.get("success") is False:
            raise ProviderError("Cloudflare reported an error while writing the story.")
        text = result if isinstance(result, str) else json.dumps(result)
        if not text.strip():
            raise ProviderError("Cloudflare returned an empty story.")
        return text

    # --- main entry ---------------------------------------------------------
    def generate_story(self, inp: StoryInput) -> Story:
        if not self.is_configured():
            raise ConfigError(self.setup_message())
        attempts: list[tuple[str, str, Callable[[str, str], str]]] = []
        if self.s.has_gemini:
            attempts.append(("Gemini", self._gemini_model, self._call_gemini))
        if self.s.has_cloudflare:
            attempts.append(("Cloudflare Llama", self.s.cloudflare_text_model, self._call_cloudflare))

        problems: list[str] = []
        for name, model, call in attempts:
            try:
                parsed = self._write(call, inp)
            except StorybookError as exc:
                problems.append(f"{name}: {exc}")
                continue
            # for Gemini the model may have been auto-corrected during the call
            used = self._gemini_model if name == "Gemini" else model
            return self._to_story(parsed, inp, used)
        raise StoryError("The story could not be written. " + " | ".join(problems))

    def _write(self, call: Callable[[str, str], str], inp: StoryInput) -> LLMStory:
        prompt = story_prompt(inp)
        try:
            return parse_story(call(STORY_SYSTEM, prompt), inp.length.page_count)
        except StoryError as exc:  # the model answered but badly: ask once more, quoting the problem
            return parse_story(call(STORY_SYSTEM, repair_prompt(prompt, str(exc))), inp.length.page_count)

    @staticmethod
    def _to_story(parsed: LLMStory, inp: StoryInput, model: str) -> Story:
        story = Story(
            title=" ".join(parsed.title.split()), introduction=" ".join(parsed.introduction.split()),
            moral=" ".join(parsed.moral.split()), character=build_character(parsed.character, inp),
            pages=[Page(page_number=i, text=p.text.strip(), scene_description=p.scene_description.strip())
                   for i, p in enumerate(parsed.pages, 1)],
            input=inp, text_model=model,
        )
        import hashlib
        story.image_seed = int(hashlib.sha256(story.story_id.encode()).hexdigest()[:8], 16) % (2**31 - 1)
        return story
