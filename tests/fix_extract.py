import fitz
from pathlib import Path

TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent

doc = fitz.open(ROOT_DIR / 'CoD.pdf')
page = doc[4]
page_w = page.rect.width
page_h = page.rect.height

# Simulated coordinates from agent.py
caption_y0 = 279.5
cap_x0_found = 317.25
cap_x1_found = 553.49
caption_y1 = 299.4

clip_x0 = 0.0
clip_x1 = page_w

if cap_x1_found < page_w / 2 + 30:     # strictly left column
    clip_x1 = page_w / 2
elif cap_x0_found > page_w / 2 - 30:   # strictly right column
    clip_x0 = page_w / 2

text_blocks = sorted([b for b in page.get_text("blocks") if b[6] == 0], key=lambda b: b[1])

fig_top = 0.0
for blk in text_blocks:
    if blk[3] < caption_y0 - 5:
        center_x = (blk[0] + blk[2]) / 2
        # Use a stricter check: the block must be mostly inside the clip region
        if center_x >= clip_x0 and center_x <= clip_x1:
            fig_top = blk[3]

fig_top = max(0.0, fig_top - 4)
clip = fitz.Rect(clip_x0, fig_top, clip_x1, min(caption_y1 + 15, page_h))

print(f"Crop: x={clip_x0:.0f}-{clip_x1:.0f}, y={fig_top:.0f}-{caption_y1:.0f}")
mat = fitz.Matrix(2, 2)
pix = page.get_pixmap(matrix=mat, clip=clip)
pix.save(TEST_DIR / "test_fix.png")
