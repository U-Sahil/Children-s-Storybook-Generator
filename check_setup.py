"""Check your .env and (optionally) test the real services.

    python check_setup.py          # reads .env only, no internet, costs nothing
    python check_setup.py --live   # also makes 1 tiny story call and ONE real image (~58 Neurons)

If something fails, the message says exactly what to fix.
"""
from __future__ import annotations

import sys

from backend.config import ROOT, load_settings
from backend.errors import StorybookError
from backend.image_gen import ImageMaker
from backend.llm import StoryWriter
from backend.models import Character, IllustrationStyle, Page
from backend.prompts import image_prompt


def line(ok: bool | None, text: str) -> None:
    print(f"  [{'OK' if ok else 'FAIL' if ok is False else '--'}] {text}")


def main() -> int:
    live = "--live" in sys.argv
    env = ROOT / ".env"
    print(f"\nProject folder : {ROOT}")
    print(f".env file      : {'found' if env.is_file() else 'NOT FOUND (copy .env.example to .env)'}")
    s = load_settings()
    print("\n1. Settings")
    line(s.has_gemini, "GEMINI_API_KEY is set" if s.has_gemini else "GEMINI_API_KEY missing or still the placeholder")
    line(s.has_cloudflare, "Cloudflare account id + token are set" if s.has_cloudflare
         else "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN missing or still placeholders")
    if not (s.has_gemini or s.has_cloudflare):
        print("\nNo story model: set GEMINI_API_KEY or the Cloudflare pair.")
    if not live:
        print("\nRun  python check_setup.py --live  to test the real services.\n")
        return 0 if s.has_cloudflare else 1

    failures = 0
    print("\n2. Story model (live)")
    writer = StoryWriter(s)
    if s.has_gemini:
        try:
            text = writer._call_gemini("Reply with the single word OK.", "Say OK.")
            line(True, f"Gemini answered using model '{writer._gemini_model}': {text.strip()[:40]!r}")
        except StorybookError as exc:
            failures += 1
            line(False, f"Gemini: {exc}")
    if s.has_cloudflare:
        try:
            text = writer._call_cloudflare("Reply with the single word OK.", "Say OK.")
            line(True, f"Cloudflare text model answered: {text.strip()[:40]!r}")
        except StorybookError as exc:
            failures += 1
            line(False, f"Cloudflare text: {exc}")
    if not (s.has_gemini or s.has_cloudflare):
        failures += 1
        line(False, "nothing to test")

    print("\n3. Image model (live, makes ONE picture)")
    maker = ImageMaker(s)
    if not maker.is_configured():
        failures += 1
        line(False, maker.setup_message())
    else:
        hero = Character(name="Aarav", age=6, hair="curly black hair", face="big brown eyes, a smile",
                         clothing="yellow raincoat and red boots", colors="yellow, red, blue",
                         distinctive_features="a red scarf")
        page = Page(page_number=1, text="x", scene_description="Aarav jumps in a puddle on a sunny street")
        try:
            png = maker._ai_image(image_prompt(hero, IllustrationStyle.CARTOON, page), 12345)
            out = s.generated_dir / "check_image.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)
            line(True, f"AI image created ({len(png) // 1024} KB). Open it: {out}")
        except StorybookError as exc:
            failures += 1
            line(False, f"Cloudflare image: {exc}")

    print("\nResult:", "everything works" if not failures else f"{failures} problem(s) above. Fix them, then run again.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
