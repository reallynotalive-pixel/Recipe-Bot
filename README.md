# recipe-pdf

Turn a social media recipe link (TikTok, Instagram, YouTube, Pinterest) **or a recipe blog/article link** into a clean, formatted PDF — no LLM involved.

## Requirements

- Python 3.10+
- System libraries for WeasyPrint (PDF rendering): Pango, Cairo, GDK-Pixbuf
  - Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`
  - macOS (Homebrew): `brew install pango cairo gdk-pixbuf`

## Setup

```bash
git clone <this repo>
cd recipe-pdf
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```bash
python3 cli.py "https://www.tiktok.com/@someone/video/123456789"
python3 cli.py "https://www.someblog.com/banana-bread-recipe"
```

The CLI automatically detects whether the link is a social media post or a regular article/blog page and routes it accordingly. The PDF is saved to `recipes/<recipe-title>.pdf`. If the recipe couldn't be confidently parsed, the CLI prints a warning and the PDF itself includes a note in the footer explaining what to double-check.

## Project structure

```
recipe-pdf/
├── cli.py                 # entry point: recipe-pdf <url>
├── fetch.py                # pulls caption/handle/platform via yt-dlp (no video download)
├── fetch_web.py             # pulls recipe + author from article/blog pages (schema.org + HTML fallback)
├── parser.py                # rule-based caption -> structured recipe data (no LLM)
├── render.py                # structured data -> PDF via Jinja2 + WeasyPrint
├── templates/
│   ├── recipe.html.j2        # PDF template
│   └── fonts/                 # Spectral + Work Sans, bundled locally
├── recipes/                    # output folder (created automatically)
└── requirements.txt
```

## How it works

**Social media links** (TikTok, Instagram, YouTube, Pinterest):
1. **Fetch** — `yt-dlp` pulls the post's caption/description and creator handle without downloading any video or image.
2. **Parse** — a set of regex/structural heuristics splits the caption into title, servings, prep/cook time, ingredients, and steps. Handles captions with explicit "Ingredients"/"Instructions" headers, bullet/numbered lists with no headers, and single-paragraph captions with no line breaks at all.

**Article/blog links** (anything else):
1. **Fetch** — the page is fetched directly (with SSRF protections — see below), following redirects manually so every hop is validated.
2. **Extract** — checks for `schema.org/Recipe` structured data (JSON-LD) first, which most recipe blogs embed for Google's recipe rich-results and gives clean, ready-to-use ingredients/steps/author/times. Falls back to scanning page headings ("Ingredients"/"Instructions") and their nearby lists if no structured data is present.

**Both paths converge** on the same structured recipe format, then:

3. **Render** — the structured data is dropped into an HTML/CSS template and rendered to PDF.

## Known limitations

- **Social media is caption-only.** If a recipe is only spoken in the video or shown as on-screen text (not written in the caption), there's nothing here to extract it from.
- **Messy captions/pages may need review.** When the parser can't find clear structure, it makes its best guess and flags the PDF with a review note in the footer — always worth a quick check on those.
- **Unitless ingredients without a recognized noun** (e.g. an uncommon countable item not in the parser's list) may get merged into the ingredient line before it.
- **Creator handles/authors** are pulled from the best available source (profile URL, schema.org data, or page meta tags); if a platform or page truly exposes nothing readable, a numeric ID or no author at all may show up.

## Security notes (article fetching)

Since `fetch_web.py` fetches arbitrary user-submitted URLs, it includes SSRF protections: every hostname (including each redirect hop) is resolved and checked against private/loopback/link-local/reserved IP ranges before any request is made. This matters most once this is exposed as a public-facing service — keep it in mind if you extend the fetch logic.

## Contact

- **Bug reports:** `o_metroboomn` on Discord
- **Contributing / joining the team** (submitting fixes, helping build things out): reallynotalive@gmail.com

## License

This work is licensed under CC BY-ND 4.0. To view a copy of this license, visit https://creativecommons.org/licenses/by-nd/4.0/
