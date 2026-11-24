"""Drive the real Streamlit script (app.py) with Streamlit's AppTest."""
import json
from pathlib import Path
from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from backend.models import Story
from backend.storage import save_story, story_dir
from conftest import cf_image_reply, gemini_reply, story_json

APP = Path(__file__).resolve().parent.parent / "app.py"
SECRETS = ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.load_dotenv", lambda *a, **k: None)   # never read a real .env
    for k in SECRETS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "gen"))
    return tmp_path / "gen"


def run(timeout=60):
    return AppTest.from_file(str(APP), default_timeout=timeout).run()


def test_no_keys_shows_setup_and_disables_generate(env):
    at = run()
    assert not at.exception
    assert any("not configured" in m.value for m in at.sidebar.markdown)
    assert at.button[0].disabled                                   # "Generate story"
    assert any("No story model is configured" in w.value for w in at.warning)


def test_defaults_match_the_five_page_demo(env):
    at = run()
    assert at.text_input[0].value == "Aarav" and at.number_input[0].value == 6
    assert "Short (5 pages)" in at.selectbox[2].options[0]


def test_full_flow_with_fake_services(env, server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    from backend import config
    real = config.load_settings
    monkeypatch.setattr("backend.config.load_settings", lambda *a, **k: replace(
        real(), gemini_base_url=f"{server.url}/v1beta", cloudflare_base_url=f"{server.url}/client/v4"))
    server.script["gemini"] = [(200, gemini_reply(json.dumps(story_json(5))))]
    server.script["cf_image"] = [(200, cf_image_reply())]

    at = run()
    assert not at.exception
    at.button[0].click().run()                                     # Generate story
    assert not at.exception and any("The Brave Little Raincoat" in s.value for s in at.subheader)

    bulk = next(b for b in at.button if "Generate all illustrations" in b.label)
    bulk.click().run()
    assert not at.exception and server.count("cf_image") == 5
    assert any("All pages illustrated" in b.label for b in at.button)

    next(b for b in at.button if "Continue to preview" in b.label).click().run()
    assert not at.exception and any("Cover</div>" in m.value for m in at.markdown)
    next(b for b in at.button if "Continue to export" in b.label).click().run()
    at.checkbox[0].check().run()
    next(b for b in at.button if "Build PDF" in b.label).click().run()
    assert not at.exception and any("PDF ready" in s.value for s in at.success)


def test_invalid_form_input_calls_no_api(env, server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    at = run()
    at.text_input[0].set_value("").run()
    at.button[0].click().run()
    assert [e.value for e in at.error] and server.requests == []


def test_open_saved_story_without_any_keys(env, story):
    save_story(env, story)
    at = run()
    next(b for b in at.sidebar.button if "Open selected story" in b.label).click().run()
    assert not at.exception and any("Brave Little Raincoat" in s.value for s in at.subheader)
