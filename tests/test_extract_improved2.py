import fitz
from pathlib import Path

TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent

doc = fitz.open(ROOT_DIR / 'CoD.pdf')
page = doc[4]
page_w = page.rect.width
page_h = page.rect.height

caption_y0 = 279.5
caption_y1 = 299.4
caption_x0 = 317.25
caption_x1 = 553.49

is_left_col = caption_x1 < page_w / 2 + 50
is_right_col = caption_x0 > page_w / 2 - 50

clip_x0 = 0
clip_x1 = page_w

if is_left_col and not is_right_col:
    clip_x1 = page_w / 2 + 20
elif is_right_col and not is_left_col:
    clip_x0 = page_w / 2 - 20

text_blocks = sorted([b for b in page.get_text("blocks") if b[6] == 0], key=lambda b: b[1])

fig_top = 0.0
for blk in text_blocks:
    if blk[3] < caption_y0 - 5:
        # Check if the block is predominantly within the column
        if blk[0] >= clip_x0 - 20 and blk[2] <= clip_x1 + 20:
            fig_top = max(fig_top, blk[3])

fig_top = max(0.0, fig_top - 4)
clip = fitz.Rect(clip_x0, fig_top, clip_x1, min(caption_y1 + 15, page_h))

print(f"Crop: x={clip_x0}-{clip_x1}, y={fig_top}-{caption_y1}")
mat = fitz.Matrix(2, 2)
pix = page.get_pixmap(matrix=mat, clip=clip)
pix.save(TEST_DIR / "test_improved2.png")
print("Saved test_improved2.png")
