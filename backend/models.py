"""Data models (Pydantic): what the user types, what the LLM returns, and the saved Story."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Theme(str, Enum):
    ADVENTURE = "Adventure"
    FRIENDSHIP = "Friendship"
    SCIENCE = "Science"
    NATURE = "Nature"
    FANTASY = "Fantasy"


class StoryLength(str, Enum):
    SHORT = "Short"
    MEDIUM = "Medium"
    LONG = "Long"

    @property
    def page_count(self) -> int:
        return {"Short": 5, "Medium": 6, "Long": 8}[self.value]


class IllustrationStyle(str, Enum):
    CARTOON = "Colorful 2D cartoon"
    WATERCOLOR = "Soft watercolor"
    CRAYON = "Crayon and pastel drawing"
    FLAT = "Flat vector storybook"


SUPPORTED_LANGUAGES = ("English",)


class StoryInput(BaseModel):
    child_name: str
    age: int = 6
    theme: Theme = Theme.ADVENTURE
    language: str = "English"
    length: StoryLength = StoryLength.SHORT
    moral: str = "Being kind and helping friends"
    character_description: str = ""
    style: IllustrationStyle = IllustrationStyle.CARTOON
    extra_instructions: str = ""

    @field_validator("child_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Child's name is required.")
        if len(v) > 40:
            raise ValueError("Child's name must be 40 characters or fewer.")
        if not v[0].isalpha() or not all(c.isalpha() or c in " .'-" for c in v):
            raise ValueError("Child's name may contain only letters, spaces, apostrophes, hyphens and dots.")
        return v

    @field_validator("age")
    @classmethod
    def _age(cls, v: int) -> int:
        if not 2 <= v <= 12:
            raise ValueError("Age must be between 2 and 12.")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        if v not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Language must be one of: {', '.join(SUPPORTED_LANGUAGES)}.")
        return v

    @field_validator("moral")
    @classmethod
    def _moral(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("Please describe the moral or learning goal (at least 3 characters).")
        if len(v) > 200:
            raise ValueError("Moral / learning goal must be 200 characters or fewer.")
        return v

    @field_validator("character_description")
    @classmethod
    def _character(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) > 400:
            raise ValueError("Character description must be 400 characters or fewer.")
        return v

    @field_validator("extra_instructions")
    @classmethod
    def _extra(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) > 500:
            raise ValueError("Additional instructions must be 500 characters or fewer.")
        return v


# ---- what we ask the LLM to return (loose on purpose; we validate afterwards) ----
class LLMCompanion(BaseModel):
    name: str = ""
    description: str = ""


class LLMCharacter(BaseModel):
    hair: str = ""
    face: str = ""
    clothing: str = ""
    colors: str = ""
    distinctive_features: str = ""
    companions: list[LLMCompanion] = Field(default_factory=list)


class LLMPage(BaseModel):
    text: str
    scene_description: str


class LLMStory(BaseModel):
    title: str
    introduction: str = ""
    character: LLMCharacter = Field(default_factory=LLMCharacter)
    pages: list[LLMPage]
    moral: str


# ---- what the app stores ----
class Companion(BaseModel):
    name: str
    description: str


class Character(BaseModel):
    name: str
    age: int
    hair: str
    face: str
    clothing: str
    colors: str
    distinctive_features: str
    companions: list[Companion] = Field(default_factory=list)


class Page(BaseModel):
    page_number: int
    text: str
    scene_description: str
    image_path: Optional[str] = None      # relative to the story folder
    image_model: Optional[str] = None
    image_is_ai: bool = False             # False => offline sketch (not an AI image)
    image_error: Optional[str] = None
    image_version: int = 0


class Story(BaseModel):
    story_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    title: str
    introduction: str
    moral: str
    character: Character
    pages: list[Page]
    input: StoryInput
    text_model: str
    image_seed: int = 0

    def pages_missing_images(self) -> list[Page]:
        return [p for p in self.pages if not p.image_path]

    def pages_without_ai_image(self) -> list[Page]:
        """No image at all, or only an offline sketch: these still need a real AI picture."""
        return [p for p in self.pages if not (p.image_path and p.image_is_ai)]
