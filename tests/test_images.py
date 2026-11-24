import base64
import io
from dataclasses import replace

import pytest
from PIL import Image

from backend.errors import ProviderError
from backend.image_gen import ImageMaker, decode_image
from backend.prompts import MAX_IMAGE_PROMPT, image_prompt
from backend.sketch import draw_sketch
from backend.storage import ai_images_today, story_dir
from conftest import cf_image_reply, png_bytes


def images_ok(server):
    server.script["cf_image"] = [(200, cf_image_reply())]


def test_request_shape_url_auth_body(server, settings, story):
    images_ok(server)
    assert ImageMaker(settings).generate_page(story, story.pages[0]) is True
    req = server.requests[0]
    assert req["path"] == "/client/v4/accounts/acct123/ai/run/@cf/black-forest-labs/flux-1-schnell"
    assert req["headers"]["Authorization"] == "Bearer cf-secret-token"
    body = req["json"]
    # FLUX schnell on Workers AI rejects extra fields such as "seed" (seen live: HTTP 400, code 5006)
    assert set(body) == {"prompt", "steps"} and body["steps"] == 4
    assert "Aarav" in body["prompt"] and "curly black hair" in body["prompt"] and "scene 1" in body["prompt"]


def test_image_saved_as_png_and_counted(server, settings, story):
    images_ok(server)
    ImageMaker(settings).generate_page(story, story.pages[0])
    page = story.pages[0]
    f = story_dir(settings.generated_dir, story) / page.image_path
    assert f.is_file() and Image.open(f).format == "PNG"
    assert page.image_is_ai and page.image_version == 1 and page.image_error is None
    assert ai_images_today(settings.generated_dir) == 1


def test_accepts_raw_image_reply(server, settings, story):
    server.script["cf_image"] = [(200, png_bytes(), "image/png")]
    assert ImageMaker(settings).generate_page(story, story.pages[0]) is True


def test_large_image_is_shrunk(server, settings, story):
    server.script["cf_image"] = [(200, cf_image_reply(png_bytes((3000, 2000))))]
    ImageMaker(settings).generate_page(story, story.pages[0])
    f = story_dir(settings.generated_dir, story) / story.pages[0].image_path
    assert max(Image.open(f).size) <= 1280


@pytest.mark.parametrize("reply", [
    (200, {"success": True, "result": {}}),                                   # no image field
    (200, {"success": False, "errors": [{"code": 1, "message": "boom"}]}),   # success false
    (200, {"success": True, "result": {"image": base64.b64encode(b"not a picture").decode()}}),
    (200, {"success": True, "result": {"image": base64.b64encode(png_bytes((8, 8))).decode()}}),  # tiny
])
def test_bad_replies_become_sketch_with_reason_not_a_crash(server, settings, story, reply):
    server.script["cf_image"] = [reply]
    page = story.pages[0]
    assert ImageMaker(settings).generate_page(story, page) is False
    assert page.image_path and not page.image_is_ai and page.image_error
    assert "offline sketch" in page.image_model


def test_auth_error_not_retried_and_token_not_leaked(server, settings, story):
    server.script["cf_image"] = [(403, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})]
    page = story.pages[0]
    ImageMaker(settings).generate_page(story, page)
    assert server.count("cf_image") == 1
    assert "Authentication error" in page.image_error and "Workers AI" in page.image_error
    assert "cf-secret-token" not in page.image_error and "acct123" not in page.image_error


def test_rate_limit_is_retried_exactly_twice(server, settings, story, no_sleep):
    server.script["cf_image"] = [(429, {"success": False, "errors": [{"code": 3036, "message": "limit"}]})]
    ImageMaker(settings).generate_page(story, story.pages[0])
    assert server.count("cf_image") == 3 and len(no_sleep) == 2
    assert "00:00 UTC" in story.pages[0].image_error


def test_503_then_success(server, settings, story):
    server.script["cf_image"] = [(503, {"errors": []}), (200, cf_image_reply())]
    assert ImageMaker(settings).generate_page(story, story.pages[0]) is True


def test_generate_all_success_makes_one_request_per_page(server, settings, story):
    images_ok(server)
    seen = []
    failed = ImageMaker(settings).generate_all(story, progress=lambda d, t, m: seen.append((d, t)))
    assert failed == [] and server.count("cf_image") == 5 and story.pages_without_ai_image() == []
    assert seen[0] == (0, 5) and seen[-1] == (5, 5)
    assert ai_images_today(settings.generated_dir) == 5


def test_generate_all_skips_pages_that_already_have_ai_images(server, settings, story):
    images_ok(server)
    m = ImageMaker(settings)
    m.generate_page(story, story.pages[0])
    m.generate_page(story, story.pages[1])
    m.generate_all(story)
    assert server.count("cf_image") == 5          # 2 + only the 3 missing ones


def test_fatal_error_stops_calls_but_every_page_still_gets_a_picture(server, settings, story):
    server.script["cf_image"] = [(401, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})]
    failed = ImageMaker(settings).generate_all(story)
    assert server.count("cf_image") == 1          # did not hammer the API 5 times
    assert failed == [1, 2, 3, 4, 5]
    assert all(p.image_path and not p.image_is_ai and p.image_error for p in story.pages)
    assert len(story.pages_without_ai_image()) == 5   # UI still offers "generate" to retry with AI


def test_not_configured_gives_sketches_and_no_network_calls(server, settings, story):
    s = replace(settings, cloudflare_api_token="")
    m = ImageMaker(s)
    assert not m.is_configured() and "CLOUDFLARE_API_TOKEN" in m.setup_message()
    failed = m.generate_all(story)
    assert server.requests == [] and len(failed) == 5 and all(p.image_path for p in story.pages)


def test_fallback_off_raises(server, settings, story):
    server.script["cf_image"] = [(403, {"errors": [{"message": "nope"}]})]
    with pytest.raises(ProviderError):
        ImageMaker(replace(settings, image_fallback=False)).generate_page(story, story.pages[0])
    assert story.pages[0].image_path is None


def test_failed_regenerate_keeps_previous_ai_picture(server, settings, story):
    images_ok(server)
    m = ImageMaker(settings)
    page = story.pages[0]
    m.generate_page(story, page)
    old = page.image_path
    server.script["cf_image"] = [(403, {"errors": [{"message": "nope"}]})]
    assert m.generate_page(story, page, user_note="more sun") is True   # still an AI picture
    assert page.image_path == old and page.image_is_ai and "kept the previous picture" in page.image_error


def test_regenerate_creates_new_version_with_note(server, settings, story):
    images_ok(server)
    m = ImageMaker(settings)
    page = story.pages[0]
    m.generate_page(story, page)
    m.generate_page(story, page, user_note="make it sunset", seed_offset=page.image_version)
    assert page.image_version == 2 and page.image_path.endswith("_v2.png")
    assert "make it sunset" in server.requests[1]["json"]["prompt"]


def test_daily_cap_blocks_ai_calls(server, settings, story):
    images_ok(server)
    m = ImageMaker(replace(settings, max_ai_images_per_day=2))
    m.generate_all(story)
    assert server.count("cf_image") == 2
    assert "Safety cap" in story.pages[4].image_error and story.pages[4].image_path


def test_prompt_respects_limit_and_never_cuts_character(story):
    story.pages[0].scene_description = "a very long scene " * 300
    p = image_prompt(story.character, story.input.style, story.pages[0], "note " * 50)
    assert len(p) <= MAX_IMAGE_PROMPT < 2048
    assert "curly black hair" in p and "yellow raincoat" in p


def test_decode_image_rejects_garbage():
    with pytest.raises(ProviderError):
        decode_image(b"hello")


@pytest.mark.parametrize("scene", ["Aarav under the stars at night", "a sunset on the beach by the sea",
                                   "snowy winter forest", "an astronaut in space", "a sunny meadow with trees"])
def test_sketch_is_valid_labelled_png_for_many_scenes(story, scene):
    story.character.companions = []
    page = story.pages[0]
    page.scene_description = scene
    img = Image.open(io.BytesIO(draw_sketch(story.character, page)))
    assert img.format == "PNG" and img.size == (1024, 768)


def test_sketch_with_companion_and_odd_colours(story):
    from backend.models import Companion
    story.character.companions = [Companion(name="Momo", description="a green parrot")]
    story.character.hair = "no colour words here"
    assert len(draw_sketch(story.character, story.pages[2])) > 5000


def test_two_failures_in_a_row_stop_the_calls_and_save_each_page(server, settings, story):
    server.script["cf_image"] = [(500, {"errors": [{"message": "down"}]})]
    saved = []
    failed = ImageMaker(settings).generate_all(story, on_page=lambda s: saved.append(sum(1 for p in s.pages if p.image_path)))
    assert server.count("cf_image") == 6          # page 1 and 2: 3 attempts each, then no more calls
    assert failed == [1, 2, 3, 4, 5] and all(p.image_path for p in story.pages)
    assert saved == [1, 2, 3, 4, 5]               # saved after every page
