# Page Frame (PER-105)

How `overlay_text.py` composites a deterministic margin/border around the artwork, instead
of leaving the model to invent one. Source of truth: `BorderSettings`, `ShadowSettings`,
`art_rect()`, and `frame_canvas()` in `skills/storybook-render/scripts/overlay_text.py` —
this doc explains the design; the code defines the numbers. See also
`docs/text-fitting.md`, whose panel geometry this rebase onto.

---

## The problem this replaces

Nothing in `story.json` ever asked for a page border. The model added one anyway — "mid-
century screen-print poster" implies printed paper, so it drew a cream margin, rounded
corners, and a drop shadow around the art. Measured inset swung 51→93px across one book,
asymmetric within single pages, absent entirely on the native cover — and it happened
*despite* `FULL_BLEED_ART_DIRECTIVE` explicitly asking for edge-to-edge art. A book cannot
have a house style that swings 80% page to page, and it cannot repeat what nothing
specified.

**Fix: stop generating it, composite it.** `render_book.py`'s `NO_FRAME_DIRECTIVE` — an
**unconditional** guard, unlike `scene_text`'s "suppress"/"allow" — bans the model from
drawing any paper margin, border, frame, mat, drop shadow, or simulated print edge, on
every page, every mode. The frame itself, if any, is drawn afterward in Pillow, from an
optional top-level `border` object.

Keeping the model-side ban unconditional is what makes `border` a free knob: art is always
requested full-bleed, so turning the frame on/off, or tuning its width/colour/shadow, is
pure Pillow work — a re-composite, never a re-render.

## The art rect

A border shrinks where the artwork *sits* within the canvas — it never changes the canvas
size itself. `art_rect(w, h, border)` returns `(x0, y0, x1, y1)`, the rectangle the artwork
occupies:

```python
def art_rect(w, h, border):
    if border is None or border.width <= 0:
        return (0, 0, w, h)              # full-bleed — today's default
    m = min(border.width, (min(w, h) - 1) // 2)   # never collapse the art to nothing
    return (m, m, w - m, h - m)
```

`border=None` (the default, or an explicit `width: 0`) returns the full canvas — this is
the identity path every no-border book stays on, byte-for-byte.

**Every panel/text geometry `_compose()` computes is now relative to the art rect, not the
canvas** — `edge_margin`, `max_box_h`, the horizontal text column, and each anchor's
vertical placement (see `docs/text-fitting.md`'s "Growth anchors"). With no border the art
rect equals the canvas, so this reproduces the pre-PER-105 formulas exactly. With a border
set, the text panel is additionally clipped to the art rect's own rounded shape — the band
can no longer spill onto the margin, which is the concrete defect this whole change exists
to fix (a page whose text sat over both the artwork and the model's invented cream margin).

## Compositing a frame

`frame_canvas(art, w, h, rect, border)` builds the framed canvas: a flat margin in
`border.color`, an optional soft drop shadow, and the artwork itself pasted into `rect`
with rounded corners (`border.radius`). It does **no resampling** — `art` must already be
exactly `rect`-sized when it's called. Fitting `art` into that size is the caller's job, and
the two callers fit differently:

- **Art pages** (`overlay()`, and the frame-only pages below): the raw art is
  **resized** down to the art rect — nothing may be lost, since a native-mode page may have
  model-lettered story text near an edge that a crop would clip.
- **Long-mode text pages** (`text_page()`): the shared background is **scale-to-cover +
  center-cropped** straight to the art rect's dimensions — it's a texture, not
  composition-critical, and `text_page()` already crops it to match its art page's canvas
  size. Cropping straight to the (possibly smaller) art rect, rather than to the full canvas
  and then resizing again for the frame, keeps this to exactly one resample regardless of
  whether a border is set.

## Frame-only pages: native and long-body art

`overlay_text.py` used to see only band pages, the long-mode cover, and long-mode text
pages — native pages and long-mode body art bypassed it entirely, written straight from the
model's bytes. PER-105 gave both a **raw/final split**, mirroring overlay's existing
`raw-page-NN.png` → `page-NN.png`:

| mode | raw (model output) | final (composited) |
|---|---|---|
| native | `raw-page-NN-native.png` | `page-NN-native.png` |
| long body art | `raw-page-NN-long.png` | `page-NN-long.png` |

Framing these is `overlay()` called with `text=""` — `_compose()` applies the border step
*before* its empty-text early return, so an empty-text `overlay()` call is exactly the
framing subset of a normal one. `render_book.py`'s `run_frame()` wraps this; when no border
applies it's a plain `shutil.copyfile` — no subprocess, no re-encode, byte-identical to the
direct write these two modes did before PER-105. This is also why both modes now have a free
re-composite path they never had: `rm page-NN-native.png` (keeping the raw) re-frames for
free; deleting both forces a paid re-render.

## Settings — `story.json`'s `border` object

```json
"border": {
  "width": 64,
  "color": "#FFEDC7",
  "radius": 48,
  "shadow": { "offset": 12, "blur": 24, "opacity": 0.25 }
}
```

**Presence is the switch.** An absent `border` means today's full-bleed behaviour — no
frame at all. A present one (even `{}`) turns the frame on, filling any omitted key from
`overlay_text.py`'s own `DEFAULT_BORDER_*`/`DEFAULT_SHADOW_*` constants — same "presence
turns it on, omission means default" contract as `layout`, but note the difference:
`layout` always applies (it sizes the panel that's drawn regardless), while `border` is
this feature's own on/off flag. `shadow` follows the same rule at its own level: absent
means no shadow at all, not a zero-opacity one.

`width`, `radius`, `shadow.offset`, and `shadow.blur` are reference-unit px, scaled by the
same `min(page_w, page_h) / reference_size` factor as every `layout` length —
**`border` does not declare its own `reference_size`**; it shares `layout.reference_size` (or
the built-in default, `2048`, if `layout` is absent too). One unit baseline per book.
`color` is a literal `#RRGGBB` hex string, never auto-sampled from the art or resolved from
`style_guide.palette` — auto-sampling would reintroduce the exact page-to-page
inconsistency this feature exists to remove.

A per-page `border: "none"` opts that one page out of an otherwise-bordered book (e.g. a
full-bleed cover); unset inherits the book setting. It's a harmless no-op on a book with no
`border` object at all.

## ⚠️ Existing books: don't double-frame

A book rendered before PER-105 has the model-invented margin **baked into its pixels** —
there is no detection for this (an edge-pixel heuristic would just be a second unrepeatable
mechanism, the opposite of the point). Setting `border` on such a book composites a second
frame on top of the first. Re-render the affected pages (delete their raws, or run a full
`render_book.py`) before adding a border — the strengthened `NO_FRAME_DIRECTIVE` removes the
generated one on the next render.

## CLI flags

Mirrors `layout`'s CLI-flag pattern in `overlay_text.py`, but with an explicit on/off
toggle (`--frame`/`--shadow`) rather than presence-of-any-flag, since `border`'s absence
must reproduce byte-identical full-bleed output regardless of what else is passed:

```
--frame                 turn the page frame on (omit for full-bleed)
--border-width PX       margin width at --reference-size (default 64)
--border-color HEX      margin colour, #RRGGBB (default #FFFFFF)
--border-radius PX      artwork corner radius at --reference-size (default 48)
--shadow                add a drop shadow (--frame only)
--shadow-offset PX      shadow offset at --reference-size (default 12)
--shadow-blur PX        shadow blur radius at --reference-size (default 24)
--shadow-opacity FLOAT  shadow opacity 0-1 (default 0.25)
```

`render_book.py`'s `resolve_border_flags(story, page)` builds these from `story.json`,
following `resolve_layout_flags`'s contract exactly: a key present in `story["border"]`
becomes a flag; an absent key is never passed, so `overlay_text.py`'s own default silently
applies. There is no CLI override on `render_book.py` itself — tune a border by editing
`story.json` and re-running `--composite-only` (free) or the editor's "Re-composite (free)"
button.

## Test

Free, no API calls — like `layout`, border compositing is Pillow-only, provable with
`--composite-only` on an existing book before any re-render. `tests/unit/test_page_frame.py`
covers `art_rect`, `frame_canvas`, reference-unit scaling, `resolve_border_flags`, and — the
load-bearing invariant — that `border=None` reproduces byte-identical output to calling
`overlay()`/`text_page()` with no border argument at all.
