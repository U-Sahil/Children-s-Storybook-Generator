"""Local files: one folder per story under generated/.

generated/<id>/story.json   the Story
generated/<id>/images/      one PNG per illustration version
generated/<id>/<title>.pdf  the exported book
generated/usage.json        how many AI images were made today (UTC)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Story


def story_dir(root: Path, story: Story | str) -> Path:
    return root / (story if isinstance(story, str) else story.story_id)


def images_dir(root: Path, story: Story) -> Path:
    path = story_dir(root, story) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_story(root: Path, story: Story) -> Path:
    folder = story_dir(root, story)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "story.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(story.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(target)  # never leaves a half-written file
    return target


def load_story(root: Path, story_id: str) -> Story:
    return Story.model_validate_json((story_dir(root, story_id) / "story.json").read_text(encoding="utf-8"))


def list_saved_stories(root: Path) -> list[tuple[str, str, str]]:
    """(story_id, title, created_at) of every readable story, newest first."""
    found = []
    if root.is_dir():
        for child in root.iterdir():
            file = child / "story.json"
            if file.is_file():
                try:
                    s = Story.model_validate_json(file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - skip a corrupt file instead of crashing the UI
                    continue
                found.append((s.story_id, s.title, s.created_at))
    return sorted(found, key=lambda t: t[2], reverse=True)


def safe_filename(title: str, fallback: str = "storybook") -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:60] or fallback


# ---- daily AI-image counter (Cloudflare's free allowance resets at 00:00 UTC) ----
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ai_images_today(root: Path) -> int:
    try:
        data = json.loads((root / "usage.json").read_text(encoding="utf-8"))
        return int(data.get("count", 0)) if data.get("date") == _today() else 0
    except (OSError, ValueError, TypeError):
        return 0


def add_ai_image(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    count = ai_images_today(root) + 1
    (root / "usage.json").write_text(json.dumps({"date": _today(), "count": count}), encoding="utf-8")
    return count
