import fitz
import sys
doc = fitz.open(sys.argv[1])
page_num = int(sys.argv[2])
page = doc[page_num]
for img in page.get_image_info():
    print(img.get('bbox'))
