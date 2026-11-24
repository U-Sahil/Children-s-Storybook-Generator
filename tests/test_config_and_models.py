from backend.config import clean_secret, load_settings
from backend.errors import InputError
from backend.models import Story, StoryInput, StoryLength
from backend.storage import (add_ai_image, ai_images_today, list_saved_stories, load_story, safe_filename,
                             save_story)
from backend.validators import validate_story_input
import pytest


def test_clean_secret_strips_quotes_spaces_and_placeholders():
    assert clean_secret('  "abc123"  ') == "abc123"
    assert clean_secret("your-gemini-api-key-here") == ""
    assert clean_secret("your-cloudflare-account-id-here") == ""
    assert clean_secret("<paste key>") == ""
    assert clean_secret(None) == ""


def test_env_file_beats_stale_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "old-stale-value")
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY="new-real-value"\nCLOUDFLARE_ACCOUNT_ID=acct\nCLOUDFLARE_API_TOKEN=tok\n')
    s = load_settings(env)
    assert s.gemini_api_key == "new-real-value"
    assert s.has_cloudflare


def test_placeholders_in_env_mean_not_configured(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=your-gemini-api-key-here\nCLOUDFLARE_ACCOUNT_ID=your-cloudflare-account-id-here\n"
                   "CLOUDFLARE_API_TOKEN=your-cloudflare-api-token-here\n")
    s = load_settings(env)
    assert not s.has_gemini and not s.has_cloudflare


def test_secrets_never_appear_in_repr_or_summary(settings):
    assert "g-secret-key" not in repr(settings) and "cf-secret-token" not in repr(settings)
    assert "g-secret-key" not in str(settings.summary())


def test_valid_input_and_defaults():
    inp = validate_story_input({"child_name": "  Aarav ", "age": 6, "theme": "Friendship", "language": "English",
                                "length": "Short", "moral": "Being kind", "character_description": "",
                                "style": "Soft watercolor", "extra_instructions": ""})
    assert inp.child_name == "Aarav" and inp.length.page_count == 5


def test_page_counts_are_small():
    assert [l.page_count for l in StoryLength] == [5, 6, 8]


@pytest.mark.parametrize("bad", [
    {"child_name": ""}, {"child_name": "R2<script>"}, {"age": 1}, {"age": 99}, {"moral": "x"},
    {"language": "French"}, {"theme": "Horror"}, {"character_description": "x" * 401},
])
def test_bad_input_is_rejected_with_readable_messages(bad):
    data = {"child_name": "Aarav", "age": 6, "theme": "Nature", "language": "English", "length": "Short",
            "moral": "Being kind", "character_description": "", "style": "Soft watercolor", "extra_instructions": ""}
    data.update(bad)
    with pytest.raises(InputError) as e:
        validate_story_input(data)
    assert e.value.problems and all(":" in p for p in e.value.problems)


def test_all_problems_reported_together():
    with pytest.raises(InputError) as e:
        validate_story_input({"child_name": "", "age": 1, "theme": "Nature", "language": "English",
                              "length": "Short", "moral": "x", "style": "Soft watercolor"})
    assert len(e.value.problems) >= 3


def test_save_load_roundtrip_and_listing(tmp_path, story):
    save_story(tmp_path, story)
    assert load_story(tmp_path, story.story_id) == story
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "story.json").write_text("{not json")
    assert [s[0] for s in list_saved_stories(tmp_path)] == [story.story_id]  # corrupt file skipped


def test_daily_counter(tmp_path):
    assert ai_images_today(tmp_path) == 0
    add_ai_image(tmp_path)
    assert add_ai_image(tmp_path) == 2 and ai_images_today(tmp_path) == 2


def test_safe_filename():
    assert safe_filename("Aarav's Big/Day!") == "Aarav_s_Big_Day"
    assert safe_filename("???") == "storybook"
