# Children's Book Style Primer

## Age → word count guide

| Age band | Total words | Words per spread (native/overlay) | Words per spread (long mode) | Font size hint |
|----------|-----------|------------------------------------|-------------------------------|----------------|
| 3–5      | 300–600    | 25–50            | 60–100   | large (~18 pt+)|
| 5–8      | 500–1000   | 40–80            | 80–200   | standard (~16 pt+)|

Default 8 spreads. Spread 1 = cover (text: title + author only, image_prompt: full cover scene). Spreads 2–7 = story body. Spread 8 = closing/back cover (short wrap, 1 sentence max).

**Long mode word counts:** the text-only page has a centered panel that can grow to ~80% of the page height and starts from a 96px font ceiling, so it comfortably carries 80–200 words per logical page. Use `text_mode: "long"` for chapter-book-style stories or longer prose that would be cramped in a ¼-page band.

## Text placement

Three patterns — pick per spread, stay consistent within the book:

- **floating** (default, native mode): the image model places the text wherever it best suits the composition — in open sky, along an edge or corner, away from faces. Most expressive and visually appealing. Native mode only; the overlay path degrades this to `bottom`.
- **bottom**: text band occupies the bottom 20–25 % of the image. Best for landscape/action scenes.
- **top**: text band at the top 20–25 %. Use for scenes where the ground line is important (character standing, walking).

## Image prompt: content only

Write `image_prompt` for scene content only — characters, setting, mood, art style. **Do not** include text-position or safe-zone language (such as "Leave the … quarter for overlaying text") — the render script appends that transparently from the page's `text_placement` field.

**Do not** put the narrative story `text` into `image_prompt`. The script handles text rendering — baked into the illustration in native mode, or Pillow-composited post-generation in overlay mode. Only diegetic text (signs, labels, book titles visible in the scene) may be part of the illustration.

## Typography rules (enforced by overlay_text.py)

- Left-aligned, ragged right — no full justification.
- No all-caps for body text. No italics.
- Semi-transparent white rounded box behind text for contrast on any background.
- Font: Andika-Regular (literacy-tested, clear letterforms) for body. PatrickHand-Regular for titles/display.
- Per-page `font` field selects the *role*: `"reader"` (body, default) or `"display"` (titles). Set the cover/title page to `"display"`; leave body pages on `"reader"` (or omit — it defaults).
- Per-page `text_align`: `"left"` (default) or `"center"`. Center the cover/title; leave body pages left-aligned.
- Optional top-level `fonts` map redefines what each role's font is, e.g. `"fonts": {"reader": "Arial", "display": "Patrick Hand"}`. Family names resolve at render time: bundled asset → system-installed font → bundled role default. No manual install needed for system fonts (Arial, Georgia, …); unknown names fall back to the bundled font. Omit `fonts` to keep the bundled Andika/PatrickHand.

## Style guide (`style_guide`) — book-wide consistency anchor (REQUIRED)

Each page is rendered in a **separate stateless API call**. There is no shared state between
calls. The only mechanism that makes all pages look like one book is injecting the **exact
same style descriptor** into every call — this is what Google's own
[Book_illustration cookbook](https://github.com/google-gemini/cookbook) does.

The **required** top-level `style_guide` object encodes this descriptor as structured
fields. Both `make_style_sheet.py` and `render_book.py` assemble a single byte-identical
block from it (fixed field order) and inject it verbatim into every call. **Both paid
scripts refuse to run when it is missing** — author it for every book. The `style` string
stays as a short human-readable label used in prose.

```json
"style_guide": {
  "medium": "soft watercolor with thin pen-and-ink outline",
  "palette": ["warm cream #F5E9D4", "sage green #8FAF85", "dusty coral #E8917A"],
  "line": "thin sepia ink, even weight, rounded corners, no crosshatching",
  "lighting": "golden-hour side-light, soft warm shadows, no harsh edges",
  "mood": "cozy, gentle, storybook calm, slightly naive brushwork"
}
```

| Field | What to write |
|---|---|
| `medium` | Rendering technique: medium + any secondary process, e.g. "gouache with digital color" |
| `palette` | 3–5 swatches as named colors or hex. **Hex is more precise** — the model anchors on it better. |
| `line` | Line weight, color, corner style; "none" if fully painterly |
| `lighting` | Direction, quality, color temperature — keep it identical across every scene |
| `mood` | Rendering vocabulary and feel; these adjectives carry into every prompt |

**Why specificity matters:** a vague `style` like `"watercolor picture book"` leaves the
model to reinterpret it every call. A specific `style_guide` with hex palette and lighting
direction gives the model the same concrete target on every page.

### Deriving style_guide from a reference image

When the user provides a style reference image (a screenshot, illustration, or any image
showing the look they want), view it in-session and extract the fields as follows:

- **`palette`** — pick 3–5 dominant colors using their hex codes (e.g. `"vivid cobalt #1A3CFF"`).
  Hex is the most precise anchor for the renderer; named-color descriptions are a fallback.
- **`medium`** — identify the rendering technique: watercolor wash, cel-shaded flat fill,
  pixel art, ink hatching, gouache, comic halftone, etc.
- **`line`** — observe line weight, color, and style: bold black outlines, thin grey ink,
  no outlines (fully painterly), dotted screen lines, etc.
- **`lighting`** — note direction and quality: flat/even (common in anime/pixel), dramatic
  side-light, diffuse overcast, hard comic shadows, etc.
- **`mood`** — capture the overall vocabulary: playful, high-contrast, gritty, soft, vibrant,
  muted, retro, etc.

The image is analyzed in-session only — it is not persisted and not passed to the renderer.
Only the extracted text ends up in `story.json`; that text is what locks book-wide visual
consistency across every separate paid API call.

## Illustration style tips

- Name characters consistently in every `image_prompt` (exact same name every page).
- Mention the style in every prompt: e.g. "soft watercolor, gentle pastel palette, children's picture book".
- Keep backgrounds simple so text overlay zone has low detail.
- Resolution default is 2K — good for print at ~8"×8" and screen. Set the optional top-level `resolution` field in `story.json` (`"1K"`, `"2K"`, or `"4K"`) to lock quality for the book; the render CLI `--resolution` flag overrides it. Use `1K` for fast/cheap drafts, `4K` for large-format print.
- Aspect ratio: square (`1:1`) works well for picture books; portrait (`3:4`, `4:5`) for tall layouts; landscape (`4:3`, `16:9`) for wide spreads. Set the optional top-level `aspect_ratio` field in `story.json` to lock framing book-wide; the render/stylesheet CLI `--aspect-ratio` flag overrides it. When omitted the model picks framing per call.
