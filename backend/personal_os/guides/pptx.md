# Producing a .pptx with python-pptx

Use `python-pptx`. Build slides from the template's layouts and fill placeholders; do not draw every text box by hand.

## Skeleton (write under outputs/)
```python
from pptx import Presentation
from pptx.util import Inches, Pt
import os

prs = Presentation()                       # default 10 x 7.5 in; for 16:9 set the size first
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

s = prs.slides.add_slide(prs.slide_layouts[0])          # 0 title, 1 title+content, 5 title only, 6 blank
s.shapes.title.text = "Launch plan"
s.placeholders[1].text = "Q4 review"

s = prs.slides.add_slide(prs.slide_layouts[1])
s.shapes.title.text = "Three decisions"
tf = s.placeholders[1].text_frame
tf.text = "Ship the beta on 12 Nov"
for line in ["Hire two support staff", "Defer the redesign"]:
    p = tf.add_paragraph(); p.text = line; p.level = 0

s = prs.slides.add_slide(prs.slide_layouts[5])
s.shapes.title.text = "Revenue"
if os.path.exists("work/rev.png"):                     # made as in the charts guide
    s.shapes.add_picture("work/rev.png", Inches(1), Inches(1.6), width=Inches(8))   # width only keeps the aspect ratio
s.notes_slide.notes_text_frame.text = "Speaker notes here."
prs.save("outputs/deck.pptx")
```

## What bites
- One idea per slide. Title: at most ~60 characters. Bullets: at most 5 per slide, ~12 words each, body font 18 pt or more. If it does not fit, split the slide; do not shrink the font below 14 pt.
- Text does not auto-shrink. Budget characters: a 10 in wide box at 18 pt holds about 70 characters per line, at 24 pt about 50. Set `tf.word_wrap = True` on text boxes you create.
- Placeholder indexes differ per layout. List them: `[(p.placeholder_format.idx, p.name) for p in slide.placeholders]`. Unused empty placeholders show "Click to add text" in the editor: remove them with `el = ph._element; el.getparent().remove(el)`.
- The default template is 4:3; if you widen the slide to 16:9, reposition placeholders you rely on, or they stay at the old positions.
- Images: pass `width` or `height`, not both, so the aspect ratio holds; check the picture stays inside the slide (left + width <= slide width).
- Charts: render with matplotlib to PNG (see the charts guide) at dpi 200 and insert; native pptx charts are fiddly.
- Tables: `shapes.add_table(rows, cols, left, top, width, height)`; set each cell's font size, the default is 18 pt and overflows quickly.
- Colours via `RGBColor(0x1F, 0x4E, 0x79)`; keep contrast high and a consistent two-colour palette.
- Missing library: call `python_install` with `python-pptx`.

## Check your work (mandatory)
1. Reopen with `Presentation(path)` and assert the slide count, each title, that no slide has an empty placeholder, and that every shape lies inside the slide bounds.
2. `render_preview` with `pages="1-8"`, then `view_image` the title slide and the densest slides. Look for text overflowing its box, overlapping shapes, cropped images, low contrast.
3. Fix and repeat at most twice.
If LibreOffice is missing, the layout cannot be checked visually here: say so in your summary.
