#!/usr/bin/env python3
"""
独立测试脚本：诊断架构图提取流程
用法: python test_extract.py CoD.pdf
"""
import sys
import re
from pathlib import Path

if len(sys.argv) < 2:
    print("用法: python test_extract.py <pdf文件名>")
    print("例如: python test_extract.py CoD.pdf")
    sys.exit(1)

pdf_name = sys.argv[1]
TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent
pdf_path = (ROOT_DIR / pdf_name).resolve()

if not pdf_path.exists():
    print(f"❌ 找不到文件: {pdf_path}")
    sys.exit(1)

try:
    import fitz
    print(f"✅ pymupdf 可用")
except ImportError:
    print("❌ pymupdf 未安装，请运行 pip install pymupdf")
    sys.exit(1)

print(f"\n📄 正在分析: {pdf_path.name}")
print("=" * 60)

doc = fitz.open(str(pdf_path))
total_pages = len(doc)
print(f"总页数: {total_pages}")

# ── 扫描所有文本块，找图注 ─────────────────────────────────────────────────
CAPTION_START_RE = re.compile(
    r'^\s*(?:fig(?:ure)?\.?\s*\d+|图\s*\d+)[.:\s：]',
    re.IGNORECASE
)
ARCH_KEYWORDS = [
    # 强信号
    'overall architecture', 'overall framework', 'overall pipeline', 'overall structure',
    'proposed framework', 'proposed architecture',
    'our framework', 'our architecture', 'our model',
    'system overview', 'model overview', 'method overview', 'network architecture',
    'architecture of our', 'framework of our', 'overview of our',
    # 中等信号（图注块级别安全）
    'overview of', 'illustration of', 'the architecture', 'the framework', 'the pipeline',
    'architecture of', 'framework of', 'pipeline of', 'structure of',
    # 中文
    '整体框架', '主体架构', '整体架构', '提出的框架', '网络架构', '模型架构',
]
FIG_NUM_RE = re.compile(r'fig(?:ure)?[.\s]*?(\d+)', re.IGNORECASE)

print(f"\n🔍 扫描前 {min(20, total_pages)} 页的图注文本块...\n")

all_captions = []   # 所有图注
all_arch_matches = []  # 含架构关键词的图注 [(fig_num, page_num, cap_x0, cap_y0, cap_x1, cap_y1, kw, preview)]

for page_num in range(min(20, total_pages)):
    page = doc[page_num]
    blocks = page.get_text("blocks")
    for blk in blocks:
        if blk[6] != 0:
            continue
        blk_text = blk[4].strip()
        if not CAPTION_START_RE.match(blk_text):
            continue
        preview = blk_text[:120].replace('\n', ' ')
        all_captions.append((page_num + 1, preview))
        print(f"  第{page_num+1}页 图注: {preview}")

        blk_lower = blk_text.lower()
        for kw in ARCH_KEYWORDS:
            if kw in blk_lower:
                m = FIG_NUM_RE.search(blk_text)
                fig_num = int(m.group(1)) if m else 999
                all_arch_matches.append((fig_num, page_num, blk[0], blk[1], blk[2], blk[3], kw, preview))
                print(f"  ✅ 命中关键词: '{kw}'  →  Figure {fig_num}")
                break

print(f"\n共找到 {len(all_captions)} 个图注，其中 {len(all_arch_matches)} 个命中架构关键词")

# 按图号升序，选最小的
all_arch_matches.sort(key=lambda x: x[0])
arch_match = all_arch_matches[0] if all_arch_matches else None
if len(all_arch_matches) > 1:
    print(f"所有候选: " + ", ".join(f"Fig{x[0]}(第{x[1]+1}页,'{x[4]}'" for x in all_arch_matches))

if not all_captions:
    print("\n⚠️  没有找到任何图注！可能原因：")
    print("  1. 图注的格式不标准（比如不是以 'Figure X' 开头）")
    print("  2. PDF 文本提取失败（扫描件 PDF）")
    print("\n📋 第1-3页的原始文本（前500字）：")
    for pg in range(min(3, total_pages)):
        raw = doc[pg].get_text()[:500].replace('\n', '↵')
        print(f"  [第{pg+1}页]: {raw}\n")

# ── 显示架构图匹配结果 ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
if arch_match:
    fig_num, page_num, cap_x0, cap_y0, cap_x1, cap_y1, kw, preview = arch_match
    print(f"✅ 选中 Figure {fig_num}，位于第 {page_num+1} 页")
    print(f"   图注: {preview}")
    print(f"   关键词: '{kw}'")
    print(f"   图注 Y 坐标: {cap_y0:.1f} ~ {cap_y1:.1f}")

    page = doc[page_num]
    imgs = page.get_images()
    print(f"   该页嵌入图片数: {len(imgs)}（若为0则说明是矢量图，将渲染整页）")

    page_h = page.rect.height
    all_blocks = page.get_text("blocks")
    text_blocks = sorted([b for b in all_blocks if b[6] == 0], key=lambda b: b[1])

    # 图注底部：直接用匹配时记录的块底部 Y（cap_y1 来自 blk[3]）
    caption_y1 = cap_y1   # ← 不重新计算，避免把图注以下所有文字包进来

    # 确定图注所在的栏（Column）边界
    clip_x0 = 0.0
    clip_x1 = page.rect.width
    
    # 简单的双栏检测 heuristic：如果图注偏向一侧，则严格限制在该栏内（以中线为界）
    if cap_x1 < page.rect.width / 2 + 30:     # 左栏
        clip_x1 = page.rect.width / 2
    elif cap_x0 > page.rect.width / 2 - 30:   # 右栏
        clip_x0 = page.rect.width / 2

    # 1. 寻找正文屏障 (ceiling)，防止向上跨越到上一个段落或上一张图
    ceiling = 0.0
    text_elements = []
    for blk in text_blocks:
        if blk[3] < cap_y0 - 5:
            center_x = (blk[0] + blk[2]) / 2
            if clip_x0 <= center_x <= clip_x1:
                # 大于 200 字符认为是正文段落，形成屏障
                if len(blk[4].strip()) >= 200:
                    ceiling = max(ceiling, blk[3])
                else:
                    # 较短的文本块可能是图内标签，加入聚类（排除页眉区的文本）
                    if blk[1] > 80:
                        text_elements.append((blk[1], blk[3]))

    # 2. 收集图形元素并强力过滤无关元素
    elements = text_elements
    for img in page.get_image_info():
        bbox = img.get("bbox")
        if bbox:
            ix0, iy0, ix1, iy1 = bbox
            if ix0 <= clip_x1 and ix1 >= clip_x0 and iy1 <= cap_y0 + 5:
                # 过滤超大全页背景图
                if iy1 - iy0 >= page.rect.height * 0.8:
                    continue
                # 过滤页面顶部的极小 Logo（如会议标志）
                if iy0 < 80 and (iy1 - iy0) < 50 and (ix1 - ix0) < 150:
                    continue
                elements.append((iy0, iy1))
                
    for d in page.get_drawings():
        r = d["rect"]
        if r.x0 <= clip_x1 and r.x1 >= clip_x0 and r.y1 <= cap_y0 + 5:
            # 过滤极小噪点
            if r.width < 20 and r.height < 20:
                continue
            # 过滤跨越半页的超大背景框
            if r.height >= page.rect.height * 0.5:
                continue
            # 过滤页眉横线 (位于顶部，细长)
            if r.y0 < 80 and r.height < 10 and r.width > 100:
                continue
            # 过滤垂直列分隔线
            if r.width < 10 and r.height > 100:
                continue
            elements.append((r.y0, r.y1))

    # 3. 按底部 y1 从下到上排序，进行严格的图形聚类
    elements.sort(key=lambda x: x[1], reverse=True)
    figure_top = cap_y0
    
    if not elements:
        # 纯文本极端 fallback
        figure_top = ceiling if ceiling > 0 else 50.0
    else:
        for y0, y1 in elements:
            if y1 < ceiling:
                break  # 碰到正文屏障
            if figure_top - y1 > 60:
                break  # 间距超过 60pt，断层过大，强力切断（隔离无关元素）
            figure_top = min(figure_top, y0)

    # 4. 直接使用聚类最高点切割
    clip_y0 = max(ceiling, figure_top - 4)
    clip_y1 = max(clip_y0 + 10, cap_y0 - 2)
    clip = fitz.Rect(clip_x0, clip_y0, clip_x1, clip_y1)
    print(f"   裁剪: x={clip_x0:.0f}→{clip_x1:.0f}, y={clip_y0:.0f} → {clip_y1:.0f}  (页面高 {page_h:.0f}pt)")

    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat, clip=clip)
    out_path = WORKDIR / "vector_db" / "arch_images" / f"TEST_{pdf_path.stem}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    print(f"\n💾 已保存裁剪预览至: {out_path}")
    print(f"   文件大小: {out_path.stat().st_size // 1024} KB")
    print(f"\n👉 请打开该图片查看效果是否正确")
else:
    print("❌ 未找到含架构关键词的图注")
    print("\n📋 所有找到的图注（供参考）：")
    for pg, cap in all_captions:
        print(f"  第{pg}页: {cap}")

    print("\n💡 建议：把上面某个图注的关键词告诉我，我来更新匹配规则")

doc.close()
print("\n" + "=" * 60)
