"""Input validation helpers.

``StoryInput`` (Pydantic) does the actual checking; this module converts
Pydantic's technical errors into short messages a user can act on.
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from .errors import InputError as InputValidationError
from .models import StoryInput

_FIELD_LABELS = {
    "child_name": "Child's name",
    "age": "Age",
    "theme": "Theme",
    "language": "Language",
    "length": "Story length",
    "moral": "Moral",
    "character_description": "Character description",
    "style": "Illustration style",
    "extra_instructions": "Additional instructions",
}


def validate_story_input(data: Mapping[str, Any]) -> StoryInput:
    """Validate raw form data and return a ``StoryInput``.

    Raises ``InputValidationError`` listing every problem at once, so the user
    can fix them in one go instead of one error per submit.
    """
    try:
        return StoryInput(**dict(data))
    except ValidationError as exc:
        problems: list[str] = []
        for err in exc.errors():
            field = str(err["loc"][0]) if err.get("loc") else "input"
            label = _FIELD_LABELS.get(field, field)
            message = err["msg"].removeprefix("Value error, ")
            if err["type"] == "missing":
                message = "This field is required."
            elif err["type"] in ("int_parsing", "int_from_float", "int_type"):
                message = "Please enter a whole number."
            elif err["type"] == "enum":
                message = "Please pick one of the listed options."
            problems.append(f"{label}: {message}")
        raise InputValidationError(problems) from exc
