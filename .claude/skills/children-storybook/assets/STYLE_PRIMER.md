# Children's Book Style Primer

## Age → word count guide

| Age band | Total words | Words per spread | Font size hint |
|----------|------------|------------------|----------------|
| 3–5      | 300–600    | 25–50            | large (~18 pt+)|
| 5–8      | 500–1000   | 40–80            | standard (~16 pt+)|

Default 8 spreads. Spread 1 = cover (text: title + author only, image_prompt: full cover scene). Spreads 2–7 = story body. Spread 8 = closing/back cover (short wrap, 1 sentence max).

## Text placement

Three patterns — pick per spread, stay consistent within the book:

- **bottom** (default): text block occupies the bottom 20–25 % of the image. Illustration fills the rest. Best for landscape/action scenes.
- **top**: text at top 20–25 %. Use for scenes where the ground line is important (character standing, walking).
- **facing** (reserved for future layout modes): image left page, text right page. Not currently supported by the scripts.

## Text-safe zone rule

Every `image_prompt` **must** include this directive (scripts append it automatically, but write it in your prompts too):

> "Leave the [top|bottom] quarter of the image as a soft, low-detail, lightly-toned area suitable for overlaying text."

Do **not** bake the narrative story text into the illustration. Text is overlaid by Pillow post-generation. Only diegetic text (signs, book titles visible in the scene) may be part of the illustration.

## Typography rules (enforced by overlay_text.py)

- Left-aligned, ragged right — no full justification.
- No all-caps for body text. No italics.
- Semi-transparent white rounded box behind text for contrast on any background.
- Font: Andika-Regular (literacy-tested, clear letterforms) for body. PatrickHand-Regular for titles/display.

## Illustration style tips

- Name characters consistently in every `image_prompt` (exact same name every page).
- Mention the style in every prompt: e.g. "soft watercolor, gentle pastel palette, children's picture book".
- Keep backgrounds simple so text overlay zone has low detail.
- 2K resolution is the default — good for print at ~8"×8" and screen.
- Aspect ratio: square (1:1) works well for picture books; scripts do not force a ratio, but prompts may suggest "square composition".
