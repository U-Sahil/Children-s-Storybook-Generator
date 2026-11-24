"""Streamlit user interface.

Run from the project root:   streamlit run app.py

This file contains UI code only. All real work is delegated to the services:
    StoryWriter  (LLM)  ->  ImageMaker (image model)  ->  build_pdf (ReportLab)
"""
from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from backend.config import load_settings
from backend.errors import ConfigError as ConfigurationError, InputError as InputValidationError, StorybookError
from backend.image_gen import ImageMaker
from backend.llm import StoryWriter
from backend.models import IllustrationStyle, Story, StoryLength, Theme
from backend.pdf_builder import build_pdf
from backend.storage import (ai_images_today, list_saved_stories, load_story, safe_filename, save_story,
                             story_dir)
from backend.validators import validate_story_input

st.set_page_config(page_title="AI Children's Storybook", page_icon="📖", layout="wide")

VIEWS = ["1 · Create", "2 · Review & illustrate", "3 · Preview", "4 · Export"]

CSS = """
<style>
.book-page {background:#fff8ee;border:1px solid #f0d9b5;border-radius:14px;padding:1.6rem 1.8rem;
            box-shadow:0 4px 14px rgba(120,80,20,.12);font-family:Georgia,'Times New Roman',serif;
            color:#2b2d42;font-size:1.28rem;line-height:1.75;min-height:260px;}
.book-cover {text-align:center;padding:1rem 0 .4rem 0;font-family:Georgia,serif;color:#2b2d42;}
.book-cover h1 {font-size:2.4rem;margin-bottom:.2rem;}
.book-cover .for {color:#ef6f6c;font-style:italic;font-size:1.2rem;}
.book-cover .intro {font-style:italic;max-width:46rem;margin:.8rem auto;}
.page-no {text-align:center;color:#8d99ae;font-size:.95rem;margin-top:.4rem;}
.moral-card {background:#fff;border:2px dashed #ef6f6c;border-radius:16px;padding:2rem;text-align:center;
             font-family:Georgia,serif;font-size:1.5rem;color:#2b2d42;}
.badge {display:inline-block;padding:2px 10px;border-radius:999px;font-size:.8rem;background:#e8f5e9;color:#1b5e20;}
.badge-warn {background:#fff3e0;color:#e65100;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------
# State helpers
# ----------------------------------------------------------------------
def get_services() -> tuple:
    """Create the settings + services once per browser session."""
    if "services" not in st.session_state:
        settings = load_settings()
        st.session_state.services = (settings, StoryWriter(settings), ImageMaker(settings))
    return st.session_state.services


def init_state() -> None:
    defaults = {"story": None, "pending_view": None, "preview_index": 0, "pdf_path": None,
                "pdf_warnings": [], "reviewed": False}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def go_to(view: str) -> None:
    st.session_state.pending_view = view


def persist(story: Story, settings) -> None:
    save_story(settings.generated_dir, story)


def image_file(story: Story, page, settings) -> Path | None:
    if not page.image_path:
        return None
    path = story_dir(settings.generated_dir, story) / page.image_path
    return path if path.is_file() else None


# ----------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------
def render_sidebar(settings, story_service, image_service) -> None:
    with st.sidebar:
        st.header("📖 Storybook AI")
        st.caption("Generative AI pipeline: LLM story → AI illustrations → PDF")

        st.subheader("Setup status")
        if story_service.is_configured():
            st.markdown(f"✅ **Story model:** `{story_service.label}`")
        else:
            st.markdown("❌ **Story model:** not configured")
            st.caption(story_service.setup_message())
        if image_service.is_configured():
            st.markdown(f"✅ **Image model:** `{image_service.provider_label}`")
        else:
            st.markdown("❌ **Image model:** not configured")
            st.caption(image_service.setup_message())
        used = ai_images_today(settings.generated_dir)
        st.caption(f"AI images made today: {used} (about {used * 58:,} of 10,000 free Neurons; resets 05:30 AM IST)")

        st.divider()
        saved = list_saved_stories(settings.generated_dir)
        if saved:
            st.subheader("Saved stories")
            labels = {sid: f"{title} ({created[:10]})" for sid, title, created in saved}
            choice = st.selectbox("Open a saved story", list(labels), format_func=labels.get, key="saved_choice")
            if st.button("Open selected story"):
                try:
                    st.session_state.story = load_story(settings.generated_dir, choice)
                    st.session_state.pdf_path = None
                    st.session_state.reviewed = False
                    st.session_state.preview_index = 0
                    go_to(VIEWS[1])
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not open that story: {exc}")
        if st.session_state.story is not None and st.button("Start a new story"):
            st.session_state.story = None
            st.session_state.pdf_path = None
            st.session_state.reviewed = False
            go_to(VIEWS[0])
            st.rerun()

        st.divider()
        st.caption("All text and images are AI-generated. A human should review them before sharing.")


# ----------------------------------------------------------------------
# View 1: Create
# ----------------------------------------------------------------------
def render_create(settings, story_service) -> None:
    st.subheader("Tell us about the child and the story")
    if not story_service.is_configured():
        st.warning(story_service.setup_message())

    with st.form("story_form"):
        c1, c2, c3 = st.columns(3)
        child_name = c1.text_input("Child's name", value="Aarav", max_chars=40)
        age = c2.number_input("Age", min_value=2, max_value=12, value=6, step=1)
        language = c3.selectbox("Language", ["English"], help="Only English is supported in this version.")

        c4, c5, c6 = st.columns(3)
        theme = c4.selectbox("Theme", [t.value for t in Theme], index=1)
        length = c5.selectbox("Story length", [f"{l.value} ({l.page_count} pages)" for l in StoryLength], index=0)
        style = c6.selectbox("Illustration style", [s.value for s in IllustrationStyle])

        moral = st.text_input("Moral / learning goal", value="Being kind and helping friends", max_chars=200)
        character = st.text_area(
            "Main character description (optional)",
            placeholder="e.g. A boy with curly black hair, big brown eyes, a yellow raincoat and red boots",
            max_chars=400,
        )
        extra = st.text_area("Additional instructions (optional)",
                             placeholder="e.g. Include a talking parrot; set it by the sea", max_chars=500)
        submitted = st.form_submit_button("✨ Generate story", type="primary",
                                          disabled=not story_service.is_configured())

    if not submitted:
        return

    length_value = length.split(" (")[0]
    try:
        story_input = validate_story_input({
            "child_name": child_name, "age": age, "theme": theme, "language": language,
            "length": length_value, "moral": moral, "character_description": character,
            "style": style, "extra_instructions": extra,
        })
    except InputValidationError as exc:
        for problem in exc.problems:
            st.error(problem)
        return

    try:
        with st.spinner("Writing the story with the AI model... (usually 10-40 seconds)"):
            story = story_service.generate_story(story_input)
        persist(story, settings)
    except StorybookError as exc:
        st.error(str(exc))
        return

    st.session_state.story = story
    st.session_state.pdf_path = None
    st.session_state.reviewed = False
    st.session_state.preview_index = 0
    go_to(VIEWS[1])
    st.rerun()


# ----------------------------------------------------------------------
# View 2: Review & illustrate
# ----------------------------------------------------------------------
def apply_page_edits(story: Story, page) -> None:
    sid = story.story_id
    page.text = st.session_state.get(f"text_{sid}_{page.page_number}", page.text).strip() or page.text
    page.scene_description = (
        st.session_state.get(f"scene_{sid}_{page.page_number}", page.scene_description).strip()
        or page.scene_description
    )


def render_review(settings, story: Story, image_service) -> None:
    sid = story.story_id
    st.subheader(f"“{story.title}”")
    st.caption(f"Story generated by `{story.text_model}` · {len(story.pages)} pages")
    st.write(story.introduction)

    with st.expander("Character profile (used in every illustration prompt)", expanded=False):
        c = story.character
        a, b = st.columns(2)
        hair = a.text_input("Hair", c.hair, key=f"hair_{sid}")
        face = b.text_input("Face", c.face, key=f"face_{sid}")
        clothing = a.text_input("Clothing", c.clothing, key=f"clothing_{sid}")
        colors_ = b.text_input("Colors", c.colors, key=f"colors_{sid}")
        feats = st.text_input("Distinctive features", c.distinctive_features, key=f"feats_{sid}")
        if c.companions:
            st.caption("Companions: " + "; ".join(f"{x.name} - {x.description}" for x in c.companions))
        if st.button("Save character edits"):
            c.hair, c.face, c.clothing, c.colors, c.distinctive_features = (
                hair.strip() or c.hair, face.strip() or c.face, clothing.strip() or c.clothing,
                colors_.strip() or c.colors, feats.strip() or c.distinctive_features)
            persist(story, settings)
            st.success("Saved. New illustrations will use the updated profile.")

    # --- bulk generation ------------------------------------------------
    st.markdown("### Illustrations")
    configured = image_service.is_configured()
    if not configured:
        st.warning(image_service.setup_message())

    missing = story.pages_without_ai_image()
    left, right = st.columns([2, 3])
    bulk_label = ("🎨 All pages illustrated" if not missing else
                  f"🎨 Generate {'all' if len(missing) == len(story.pages) else 'missing'} illustrations")
    if left.button(bulk_label, type="primary", disabled=not missing):
        for page in story.pages:
            apply_page_edits(story, page)
        bar = st.progress(0.0, text="Starting...")

        def on_progress(done: int, total: int, message: str) -> None:
            bar.progress(done / total if total else 1.0, text=message)

        try:
            failed = image_service.generate_all(story, progress=on_progress, only_missing=True,
                                                on_page=lambda s: persist(s, settings))
        except StorybookError as exc:
            st.error(str(exc))
            failed = []
        persist(story, settings)
        st.session_state.reviewed = False
        st.session_state.pdf_path = None
        if failed:
            st.warning(f"Pages {failed} have no AI picture yet (see the message under each page). "
                       "Fix the problem and press the button again; only those pages are retried.")
        st.rerun()
    right.caption("Every prompt repeats the same character description to keep the hero "
                  "looking alike. This improves consistency but does not guarantee it. "
                  "One book = one image per page; the cover reuses page 1.")

    # --- per page -------------------------------------------------------
    for page in story.pages:
        n = page.page_number
        with st.container(border=True):
            col_text, col_img = st.columns([3, 2])
            with col_text:
                st.markdown(f"**Page {n}**")
                st.text_area("Story text", page.text, key=f"text_{sid}_{n}", height=110)
                st.text_area("Scene description (drives the illustration)", page.scene_description,
                             key=f"scene_{sid}_{n}", height=90)
            with col_img:
                file = image_file(story, page, settings)
                if file is not None:
                    st.image(file.read_bytes(), width="stretch")
                    st.caption(f"v{page.image_version} · {page.image_model}")
                else:
                    st.info("No illustration yet")
                if page.image_error:
                    st.error(page.image_error)
                note = st.text_input("Reviewer note (optional)", key=f"note_{sid}_{n}",
                                     placeholder="e.g. make the sky sunset orange")
                label = "🔁 Regenerate" if file else "🎨 Generate"
                if st.button(label, key=f"regen_{sid}_{n}"):
                    apply_page_edits(story, page)
                    try:
                        with st.spinner(f"Illustrating page {n}..."):
                            image_service.generate_page(story, page, user_note=note,
                                                        seed_offset=page.image_version)
                    except StorybookError as exc:
                        st.error(str(exc))
                    persist(story, settings)
                    st.session_state.reviewed = False
                    st.session_state.pdf_path = None
                    st.rerun()

    if st.button("💾 Save text edits"):
        for page in story.pages:
            apply_page_edits(story, page)
        persist(story, settings)
        st.success("Edits saved.")

    st.button("Continue to preview →", on_click=go_to, args=(VIEWS[2],))


# ----------------------------------------------------------------------
# View 3: Preview
# ----------------------------------------------------------------------
def render_preview(settings, story: Story) -> None:
    total = len(story.pages) + 2  # cover + pages + moral
    idx = max(0, min(st.session_state.preview_index, total - 1))
    st.session_state.preview_index = idx

    nav_l, nav_mid, nav_r = st.columns([1, 4, 1])
    if nav_l.button("← Previous", disabled=idx == 0, width="stretch"):
        st.session_state.preview_index = idx - 1
        st.rerun()
    if nav_r.button("Next →", disabled=idx == total - 1, width="stretch"):
        st.session_state.preview_index = idx + 1
        st.rerun()

    esc = html.escape
    if idx == 0:
        cover_page = next((p for p in story.pages if image_file(story, p, settings)), None)
        st.markdown(
            f"<div class='book-cover'><h1>{esc(story.title)}</h1>"
            f"<div class='for'>A story for {esc(story.input.child_name)}</div>"
            f"<div class='intro'>{esc(story.introduction)}</div></div>",
            unsafe_allow_html=True,
        )
        if cover_page is not None:
            _, mid, _ = st.columns([1, 2, 1])
            mid.image(image_file(story, cover_page, settings).read_bytes(), width="stretch")
        else:
            st.info("No illustrations yet - the cover will show the first illustration once generated.")
        label = "Cover"
    elif idx == total - 1:
        st.markdown(f"<div class='moral-card'>🌟 <b>The Lesson</b><br><br>{esc(story.moral)}</div>",
                    unsafe_allow_html=True)
        label = "The Lesson"
    else:
        page = story.pages[idx - 1]
        col_img, col_text = st.columns(2)
        with col_img:
            file = image_file(story, page, settings)
            if file is not None:
                st.image(file.read_bytes(), width="stretch")
            else:
                st.info("Illustration not generated for this page.")
        with col_text:
            st.markdown(f"<div class='book-page'>{esc(page.text)}</div>", unsafe_allow_html=True)
        label = f"Page {page.page_number} of {len(story.pages)}"

    st.markdown(f"<div class='page-no'>{label}</div>", unsafe_allow_html=True)
    st.button("Continue to export →", on_click=go_to, args=(VIEWS[3],))


# ----------------------------------------------------------------------
# View 4: Export
# ----------------------------------------------------------------------
def render_export(settings, story: Story) -> None:
    st.subheader("Export the storybook as PDF")
    missing = story.pages_missing_images()
    if missing:
        st.warning(
            f"{len(missing)} page(s) have no illustration ({', '.join(str(p.page_number) for p in missing)}). "
            "They will appear as a clearly labelled 'Illustration not generated' box in the PDF."
        )
    sketches = [p for p in story.pages if p.image_path and not p.image_is_ai]
    if sketches:
        st.info(f"Pages {', '.join(str(p.page_number) for p in sketches)} use a simple offline sketch, not an AI "
                "image. Go back to step 2 and press the illustration button to retry them with AI.")
    st.session_state.reviewed = st.checkbox(
        "I have reviewed the story and illustrations and they are suitable for the child",
        value=st.session_state.reviewed,
    )

    if st.button("📄 Build PDF", type="primary", disabled=not st.session_state.reviewed):
        folder = story_dir(settings.generated_dir, story)
        target = folder / f"{safe_filename(story.title)}.pdf"
        try:
            with st.spinner("Assembling the PDF..."):
                path, warnings = build_pdf(story, folder, target)
            st.session_state.pdf_path = str(path)
            st.session_state.pdf_warnings = warnings
            persist(story, settings)
        except StorybookError as exc:
            st.error(str(exc))

    if st.session_state.pdf_path and Path(st.session_state.pdf_path).is_file():
        path = Path(st.session_state.pdf_path)
        st.success(f"PDF ready: {path.name} ({path.stat().st_size / 1024:.0f} KB)")
        for warning in st.session_state.pdf_warnings:
            st.warning(warning)
        st.download_button("⬇️ Download PDF", data=path.read_bytes(), file_name=path.name,
                           mime="application/pdf", type="primary")
        st.caption(f"Also saved on disk at: {path}")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> None:
    init_state()
    settings, story_service, image_service = get_services()
    render_sidebar(settings, story_service, image_service)

    st.title("AI-Generated Children's Storybook")
    st.caption("Personalised stories and illustrations with Generative AI")

    # A view change requested by a button callback is applied *before* the radio is created.
    if st.session_state.pending_view:
        st.session_state.view = st.session_state.pending_view
        st.session_state.pending_view = None
    st.session_state.setdefault("view", VIEWS[0])

    story: Story | None = st.session_state.story
    view = st.radio("Step", VIEWS, horizontal=True, key="view", label_visibility="collapsed")

    if view == VIEWS[0]:
        render_create(settings, story_service)
    elif story is None:
        st.info("Create or open a story first (step 1).")
    elif view == VIEWS[1]:
        render_review(settings, story, image_service)
    elif view == VIEWS[2]:
        render_preview(settings, story)
    else:
        render_export(settings, story)


main()
