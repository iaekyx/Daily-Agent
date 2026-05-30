import fitz
from pathlib import Path

TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent

doc = fitz.open(ROOT_DIR / 'CoD.pdf')
page = doc[4]
caption_y0 = 279.5

drawings = page.get_drawings()
for i, d in enumerate(drawings[:10]):
    print(f"Drawing {i}: rect={d['rect']}")

images = page.get_images(full=True)
for i, img in enumerate(images):
    print(f"Image {i}: {img}")
