# Children's Book Style Primer

## Age → word count guide

| Age band | Total words | Words per spread | Font size hint |
|----------|------------|------------------|----------------|
| 3–5      | 300–600    | 25–50            | large (~18 pt+)|
| 5–8      | 500–1000   | 40–80            | standard (~16 pt+)|

Default 8 spreads. Spread 1 = cover (text: title + author only, image_prompt: full cover scene). Spreads 2–7 = story body. Spread 8 = closing/back cover (short wrap, 1 sentence max).

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

## Illustration style tips

- Name characters consistently in every `image_prompt` (exact same name every page).
- Mention the style in every prompt: e.g. "soft watercolor, gentle pastel palette, children's picture book".
- Keep backgrounds simple so text overlay zone has low detail.
- Resolution default is 2K — good for print at ~8"×8" and screen. Set the optional top-level `resolution` field in `story.json` (`"1K"`, `"2K"`, or `"4K"`) to lock quality for the book; the render CLI `--resolution` flag overrides it. Use `1K` for fast/cheap drafts, `4K` for large-format print.
- Aspect ratio: square (`1:1`) works well for picture books; portrait (`3:4`, `4:5`) for tall layouts; landscape (`4:3`, `16:9`) for wide spreads. Set the optional top-level `aspect_ratio` field in `story.json` to lock framing book-wide; the render/stylesheet CLI `--aspect-ratio` flag overrides it. When omitted the model picks framing per call.
