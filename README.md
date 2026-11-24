# AI Children's Storybook (simple version)

Type a child's name, age, theme and moral. The app writes a short story with AI, draws one AI picture per page,
lets you review and edit everything, and exports a PDF book (cover + 5 story pages + a lesson page).

```
Form -> story writer (Gemini, or Cloudflare Llama as backup) -> 5 scene descriptions
     -> Cloudflare FLUX.1 schnell: ONE image per page (cover reuses page 1)
     -> review / regenerate -> preview -> PDF (ReportLab)
```

## 1. Install (once)

Windows (PowerShell):
```
cd storybook-simple
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Mac / Linux: `source venv/bin/activate` and `cp .env.example .env`.

## 2. Get the free keys (about 5 minutes)

**Cloudflare (pictures, and a backup story writer)** - free daily allowance: 10,000 Neurons/day.
1. Sign up free at dash.cloudflare.com (no card needed for the free plan).
2. Open **AI > Workers AI**, click **Use REST API**.
3. Copy the **Account ID**. Click **Create a Workers AI API Token**, then **Create API Token**, copy the token.
4. Paste both into `.env`:
   ```
   CLOUDFLARE_ACCOUNT_ID=...
   CLOUDFLARE_API_TOKEN=...
   ```

**Gemini (story writer)** - free tier: create a key at aistudio.google.com and put it in `.env` as `GEMINI_API_KEY=...`.
If you skip it, Cloudflare writes the story instead (simpler wording, same pipeline).

Rules for `.env`: real values only (a `your-...-here` placeholder counts as "not set"), no need for quotes, save the
file, and **stop and restart the app after every change**. Never paste keys into chat or commit `.env`.

## 3. Test your keys BEFORE the demo (20 seconds)

```
python check_setup.py --live
```
It tests the story model and makes ONE real picture (about 58 Neurons) and tells you exactly what is wrong if
something fails (wrong token, wrong account id, quota used up...). The test picture is saved in `generated/check_image.png`.

## 4. Run

```
streamlit run app.py
```
Open http://localhost:8501. Steps: **1 Create -> 2 Review & illustrate -> 3 Preview -> 4 Export** (tick the review box, build the PDF, download).

## How many images? (free allowance)
A 5-page story = 5 AI images (the cover reuses page 1). Cloudflare's price list: 4.8 Neurons per 512x512 tile plus
9.6 per step; one 1024x1024 image at 4 steps is about 58 Neurons (my calculation, assuming 1024x1024), so a book is
about 300 Neurons, roughly 3% of the free 10,000/day. The allowance resets at 00:00 UTC (05:30 AM India time).
The sidebar shows how many AI images you made today; `MAX_AI_IMAGES_PER_DAY` (default 40) stops accidents.

## If the AI picture fails
The app never leaves a page empty: it draws a simple **offline sketch** (made locally with Pillow, clearly labelled
"offline sketch (not AI)", and disclosed on the PDF's last page) and shows the reason under the page. Fix the problem
and press the illustration button again; only the pages without an AI picture are retried. Turn this off with `IMAGE_FALLBACK=false`.

Common reasons: wrong/expired token, token without Workers AI permission, Account ID from another account,
free allowance used up (wait for 00:00 UTC), no internet.

## Files
| File | What it does |
|---|---|
| `app.py` | The Streamlit screens (UI only) |
| `backend/config.py` | Reads `.env` safely (placeholders ignored, `.env` beats stale system values) |
| `backend/models.py` | Data shapes: form input, story, pages, character |
| `backend/prompts.py` | All prompt text: story prompt and image prompt (same character text on every page) |
| `backend/llm.py` | Story writer: Gemini, then Cloudflare Llama; JSON check; one repair retry |
| `backend/image_gen.py` | Cloudflare FLUX call, picture check, saving versions, daily counter, fallback |
| `backend/sketch.py` | The offline sketch painter |
| `backend/net.py` | The one place for HTTP calls: retries only temporary errors, friendly messages, hides secrets |
| `backend/pdf_builder.py` | ReportLab PDF layout |
| `backend/storage.py`, `validators.py`, `errors.py` | Saving stories, input checks, error types |
| `check_setup.py` | Live key test |
| `tests/` | 69 automated tests (no real API calls, nothing paid) |

## Tests
```
pip install -r requirements-dev.txt
python -m pytest
```
**69 passed** on Linux / Python 3.13. The web-API tests run the real `requests` code against a fake local server that
copies the Cloudflare and Gemini formats from their documentation. The UI tests drive the real Streamlit script.

## Honest limits (say these to the jury)
- **Not yet verified live by the author**: the sandbox that built this could not reach Cloudflare or Google, so the live
  calls follow the official docs and fake-server tests only. Run `check_setup.py --live` once on your machine.
- **Character consistency is improved, not guaranteed.** Every prompt repeats the same character description,
  and FLUX schnell has no reference-image or seed input, so the child can look a little different per page. Not measured.
- English only; pictures are generated one after another (about 5-15 seconds each); AI text and images need human review (built in).

## Plan for demo day
1. Day before: `python check_setup.py --live`, then make one complete book and export the PDF.
2. That story stays in **Saved stories** (sidebar) and works with no internet: open it as your backup.
3. Demo: create a new story live; if Wi-Fi fails, open the saved one.
