import json
from dataclasses import replace

import pytest

from backend.errors import ConfigError, StoryError
from backend.llm import StoryWriter, extract_json, parse_story
from conftest import cf_text_reply, gemini_reply, story_json


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```\nEnjoy') == {"a": 1}
    for bad in ("no json here", '{"a": ', "[1, 2]"):
        with pytest.raises(StoryError):
            extract_json(bad)


def test_parse_story_checks_page_count_and_empty_fields():
    assert len(parse_story(json.dumps(story_json(5)), 5).pages) == 5
    with pytest.raises(StoryError, match="Expected exactly 5"):
        parse_story(json.dumps(story_json(4)), 5)
    broken = story_json(5)
    broken["pages"][2]["text"] = "  "
    with pytest.raises(StoryError, match="Page 3"):
        parse_story(json.dumps(broken), 5)
    with pytest.raises(StoryError, match="missing"):
        parse_story(json.dumps({"title": "x"}), 5)


def test_gemini_request_shape_and_result(server, settings, story_input):
    server.script["gemini"] = [(200, gemini_reply(json.dumps(story_json(5))))]
    story = StoryWriter(settings).generate_story(story_input)
    req = server.requests[0]
    assert req["headers"]["x-goog-api-key"] == "g-secret-key"            # key in header, never in the URL
    assert "g-secret-key" not in req["path"]
    assert req["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "systemInstruction" in req["json"] and "Aarav" in req["json"]["contents"][0]["parts"][0]["text"]
    assert len(story.pages) == 5 and story.character.name == "Aarav"     # name comes from the user
    assert story.character.companions[0].name == "Momo"
    assert story.text_model == "gemini-3.8-flash" and story.image_seed > 0


def test_repair_retry_after_bad_json(server, settings, story_input):
    server.script["gemini"] = [(200, gemini_reply("sorry, here is a story")),
                               (200, gemini_reply(json.dumps(story_json(5))))]
    story = StoryWriter(settings).generate_story(story_input)
    assert server.count("gemini") == 2 and len(story.pages) == 5
    assert "rejected" in server.requests[1]["json"]["contents"][0]["parts"][0]["text"]


def test_invalid_gemini_key_falls_back_to_cloudflare_story(server, settings, story_input):
    server.script["gemini"] = [(400, {"error": {"message": "API key not valid. Please pass a valid API key.",
                                                "status": "INVALID_ARGUMENT"}})]
    server.script["cf_text"] = [(200, cf_text_reply(json.dumps(story_json(5))))]
    story = StoryWriter(settings).generate_story(story_input)
    assert server.count("gemini") == 1            # a bad key is not retried
    assert story.text_model == "@cf/meta/llama-3.1-8b-instruct"
    cf = [r for r in server.requests if r["route"] == "cf_text"][0]
    assert cf["json"]["max_tokens"] >= 1500 and cf["headers"]["Authorization"] == "Bearer cf-secret-token"
    assert cf["json"]["messages"][0]["role"] == "system"


def test_invalid_gemini_key_alone_gives_actionable_message(server, settings, story_input):
    server.script["gemini"] = [(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})]
    with pytest.raises(StoryError) as e:
        StoryWriter(replace(settings, cloudflare_api_token="")).generate_story(story_input)
    text = str(e.value)
    assert "GEMINI_API_KEY" in text and "RESTART" in text and "g-secret-key" not in text


def test_retired_gemini_model_is_discovered_and_retried(server, settings, story_input):
    server.script["gemini"] = [(404, {"error": {"message": "models/gemini-3.8-flash is not found"}}),
                               (200, gemini_reply(json.dumps(story_json(5))))]
    server.script["models"] = [(200, {"models": [
        {"name": "models/gemini-9.0-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9.0-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9.0-pro", "supportedGenerationMethods": ["generateContent"]}]})]
    story = StoryWriter(settings).generate_story(story_input)
    assert story.text_model == "gemini-9.0-flash"
    assert "gemini-9.0-flash:generateContent" in server.requests[-1]["path"]


def test_503_is_retried_then_succeeds(server, settings, story_input, no_sleep):
    server.script["gemini"] = [(503, {"error": {"message": "overloaded"}}),
                               (200, gemini_reply(json.dumps(story_json(5))))]
    StoryWriter(settings).generate_story(story_input)
    assert server.count("gemini") == 2 and no_sleep


def test_both_providers_down_reports_both(server, settings, story_input):
    server.script["gemini"] = [(403, {"error": {"message": "denied"}})]
    server.script["cf_text"] = [(403, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})]
    with pytest.raises(StoryError) as e:
        StoryWriter(settings).generate_story(story_input)
    assert "Gemini:" in str(e.value) and "Cloudflare Llama:" in str(e.value)
    assert "cf-secret-token" not in str(e.value)


def test_not_configured_raises_config_error(settings, story_input):
    w = StoryWriter(replace(settings, gemini_api_key="", cloudflare_api_token=""))
    assert not w.is_configured()
    with pytest.raises(ConfigError):
        w.generate_story(story_input)


def test_gemini_blocked_reply_is_explained(server, settings, story_input):
    server.script["gemini"] = [(200, {"promptFeedback": {"blockReason": "SAFETY"}})]
    server.script["cf_text"] = [(200, cf_text_reply("not json at all"))]
    with pytest.raises(StoryError) as e:
        StoryWriter(settings).generate_story(story_input)
    assert "blocked: SAFETY" in str(e.value)
