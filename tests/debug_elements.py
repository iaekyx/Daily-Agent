import sys
import fitz

doc = fitz.open(sys.argv[1])
page = doc[int(sys.argv[2])]
cap_y0 = float(sys.argv[3])

elements = []
for img in page.get_image_info():
    bbox = img.get("bbox")
    if bbox:
        ix0, iy0, ix1, iy1 = bbox
        if iy1 <= cap_y0 + 5:
            elements.append((iy0, iy1, "img"))

for d in page.get_drawings():
    r = d["rect"]
    if r.y1 <= cap_y0 + 5:
        if r.width > 20 or r.height > 20:
            elements.append((r.y0, r.y1, "draw"))

elements.sort(key=lambda x: x[1], reverse=True)
for e in elements:
    print(e)
