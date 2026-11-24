import json

import pytest
from pypdf import PdfReader

from backend.errors import PDFError
from backend.image_gen import ImageMaker
from backend.llm import StoryWriter
from backend.pdf_builder import build_pdf, sanitize_text
from backend.storage import load_story, save_story, story_dir
from conftest import cf_image_reply, gemini_reply, story_json


def make_pdf(settings, story):
    folder = story_dir(settings.generated_dir, story)
    return build_pdf(story, folder, folder / "book.pdf")


def text_of(path):
    return " ".join(p.extract_text() for p in PdfReader(str(path)).pages)


def test_pdf_has_cover_pages_and_lesson(server, settings, story):
    server.script["cf_image"] = [(200, cf_image_reply())]
    ImageMaker(settings).generate_all(story)
    path, warnings = make_pdf(settings, story)
    reader = PdfReader(str(path))
    assert len(reader.pages) == 5 + 2 and warnings == []
    body = text_of(path)
    assert "The Brave Little Raincoat" in body and "A story for Aarav" in body and "The Lesson" in body
    assert "illustrations were generated with AI" in body
    assert len(reader.pages[1].images) == 1              # real picture on a story page


def test_missing_images_are_labelled_not_faked(settings, story):
    path, warnings = make_pdf(settings, story)
    assert "Illustration not generated" in text_of(path) and warnings


def test_sketch_pages_are_disclosed_in_pdf(server, settings, story):
    server.script["cf_image"] = [(403, {"errors": [{"message": "no"}]})]
    ImageMaker(settings).generate_all(story)
    path, warnings = make_pdf(settings, story)
    assert any("offline sketch" in w for w in warnings)
    assert "offline sketches" in text_of(path)


def test_corrupt_image_file_does_not_break_pdf(server, settings, story):
    server.script["cf_image"] = [(200, cf_image_reply())]
    ImageMaker(settings).generate_all(story)
    (story_dir(settings.generated_dir, story) / story.pages[1].image_path).write_bytes(b"garbage")
    path, warnings = make_pdf(settings, story)
    assert path.is_file() and any("unreadable" in w for w in warnings)


def test_very_long_text_and_special_characters(settings, story):
    story.pages[0].text = "Once upon a time, a very long sentence continued. " * 400
    story.pages[1].text = "Emoji 😀 and “quotes” — dashes… नमस्ते"
    path, _ = make_pdf(settings, story)
    assert len(PdfReader(str(path)).pages) > 7
    assert "quotes" in text_of(path)
    assert sanitize_text("“hi” — ok…") == '"hi" - ok...'


def test_empty_story_and_unwritable_path(settings, story, tmp_path):
    story.pages = []
    with pytest.raises(PDFError):
        build_pdf(story, tmp_path, tmp_path / "x.pdf")


def test_end_to_end_with_fake_services(server, settings, story_input):
    """Form input -> LLM story -> 5 AI images (one fails first, then regenerated) -> save/load -> PDF."""
    server.script["gemini"] = [(200, gemini_reply(json.dumps(story_json(5))))]
    story = StoryWriter(settings).generate_story(story_input)
    save_story(settings.generated_dir, story)

    maker = ImageMaker(settings)
    server.script["cf_image"] = [(200, cf_image_reply()), (200, cf_image_reply()), (500, {"errors": []}),
                                 (500, {"errors": []}), (500, {"errors": []}), (200, cf_image_reply())]
    failed = maker.generate_all(story)
    assert failed == [3]                                  # page 3 failed all 3 attempts -> sketch
    assert story.pages[2].image_path and not story.pages[2].image_is_ai
    assert maker.generate_all(story) == []                # retry only the missing AI page
    assert story.pages_without_ai_image() == []

    save_story(settings.generated_dir, story)
    reloaded = load_story(settings.generated_dir, story.story_id)
    path, warnings = make_pdf(settings, reloaded)
    assert len(PdfReader(str(path)).pages) == 7 and warnings == []
