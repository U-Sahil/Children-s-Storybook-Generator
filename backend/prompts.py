"""All prompt text in one place (prompt engineering is separate from code logic)."""
from __future__ import annotations

from .models import Character, IllustrationStyle, Page, StoryInput

STORY_SYSTEM = """\
You are an award-winning children's picture-book author and an illustrator's art director.
Write warm, imaginative, age-appropriate stories and describe every page as one concrete scene.

Hard rules:
- Safe for children: no violence, horror, romance or adult content. Gentle challenges, happy endings.
- Answer with ONE JSON object only. No markdown, no commentary before or after it.
- Every page has 'text' (narration for the child) and 'scene_description' (what the picture shows:
  setting, who is there, the action, the mood, the light).
- In every scene_description use the hero's name and keep the appearance defined in 'character'.
- Pictures cannot contain readable text, so never ask for signs, letters or books with words.
"""


def _reading_level(age: int) -> str:
    if age <= 4:
        return "Toddler reader: very simple words, short sentences, gentle repetition, 15-30 words per page."
    if age <= 7:
        return "Early reader: simple words, 2-4 short sentences per page, 35-60 words per page."
    return "Confident reader: richer vocabulary, 3-5 sentences per page, 60-100 words per page."


def story_prompt(inp: StoryInput) -> str:
    n = inp.length.page_count
    look = inp.character_description or "Invent a friendly, distinctive look (hair, face, outfit, colours)."
    return f"""\
Write a personalised illustrated children's story.

Hero: {inp.child_name}, age {inp.age}
Theme: {inp.theme.value}
Language: {inp.language}
Moral: {inp.moral}
Hero's appearance (from the parent): {look}
Extra instructions: {inp.extra_instructions or "None."}
Reading level: {_reading_level(inp.age)}

Rules:
- EXACTLY {n} items in "pages".
- Page 1 introduces the hero and place, the middle has a gentle problem, the last page resolves it and shows the moral.
- "character" describes the hero ONCE (hair, face, clothing, colors, distinctive_features) and at most
  two recurring companions (name + exact visual description; empty list if none).

Return JSON with exactly this shape:
{{"title": str, "introduction": str,
  "character": {{"hair": str, "face": str, "clothing": str, "colors": str, "distinctive_features": str,
                 "companions": [{{"name": str, "description": str}}]}},
  "pages": [{{"text": str, "scene_description": str}}],
  "moral": str}}
"""


def repair_prompt(original: str, problem: str) -> str:
    return (f"{original}\n\nYour previous answer was rejected: {problem}\n"
            "Return ONLY the corrected, complete JSON object.")


STYLE_TEXT = {
    IllustrationStyle.CARTOON: "colorful 2D cartoon children's book illustration, bold clean outlines, bright cheerful colors",
    IllustrationStyle.WATERCOLOR: "soft watercolor children's book illustration, gentle color washes, warm light",
    IllustrationStyle.CRAYON: "hand-drawn crayon and pastel children's book illustration, textured strokes, playful",
    IllustrationStyle.FLAT: "flat vector storybook illustration, simple shapes, harmonious limited palette",
}

MAX_IMAGE_PROMPT = 1800  # Cloudflare FLUX accepts at most 2048 characters


def describe_character(c: Character) -> str:
    return (f"{c.name}, a {c.age}-year-old child. Hair: {c.hair}. Face: {c.face}. Clothing: {c.clothing}. "
            f"Colors: {c.colors}. Distinctive: {c.distinctive_features}.")


def image_prompt(c: Character, style: IllustrationStyle, page: Page, note: str = "") -> str:
    """Same character text on every page (consistency), plus this page's scene. Never longer than the limit."""
    companions = ""
    if c.companions:
        companions = " Companions (always look the same): " + " ".join(
            f"{x.name}: {x.description}." for x in c.companions)
    extra = f" Extra direction: {note.strip()}." if note.strip() else ""

    def build(scene: str) -> str:
        return (f"{STYLE_TEXT[style]}. MAIN CHARACTER (identical on every page): {describe_character(c)}"
                f"{companions} SCENE: {scene}.{extra} Landscape full-scene illustration, the character clearly "
                "visible, friendly mood, child-safe. No text, letters, captions or watermarks.")

    scene = " ".join(page.scene_description.split()).rstrip(".")
    prompt = build(scene)
    if len(prompt) > MAX_IMAGE_PROMPT:  # shorten the scene, never the character description
        room = max(60, len(scene) - (len(prompt) - MAX_IMAGE_PROMPT) - 3)
        prompt = build(scene[:room].rsplit(" ", 1)[0] + "...")
    return prompt[:MAX_IMAGE_PROMPT]
