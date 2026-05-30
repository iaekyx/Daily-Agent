from __future__ import annotations

import json
import re
from pathlib import Path

import chromadb
try:
    import fitz
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False
    print("[\033[33mWarning\033[0m] pymupdf 未安装，架构图提取功能将不可用。运行 'pip install pymupdf' 以启用。")
from pypdf import PdfReader

from .config import MODEL, WORKDIR, client
from .fs import safe_path

# =====================================================================
# 新增模块：长期记忆库 (Long-term Memory / RAG)
# =====================================================================
# 初始化本地向量数据库（数据会持久化保存在当前目录的 vector_db 文件夹中）
CHROMA_PATH = WORKDIR / "vector_db"
chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
# 获取或创建个人论文合集
paper_collection = chroma_client.get_or_create_collection(name="personal_papers")
# 架构图存储目录
ARCH_IMAGES_DIR = CHROMA_PATH / "arch_images"
ARCH_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

def build_paper_search_document(title: str, summary: str, metadata: dict | None = None) -> str:
    """Build the searchable text stored in Chroma."""
    metadata = metadata or {}
    fields = [
        ("Title", title),
        ("Source", metadata.get("source", "")),
        ("Type", metadata.get("type", "")),
        ("Direction Tags", metadata.get("direction_tags", "")),
        ("Summary", summary or metadata.get("summary", "")),
        ("User Comment", metadata.get("comment", "")),
        ("Read At", metadata.get("read_at", "")),
    ]
    return "\n".join(f"{name}: {value}" for name, value in fields if value)

def normalize_direction_tags(value) -> list:
    if isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = re.split(r"[,，、;；\s]+", str(value or ""))
    tags = []
    for tag in raw_tags:
        cleaned = str(tag or "").strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags[:8]

def generate_paper_direction_tags(title: str, summary: str, source: str = "") -> list:
    """Use the LLM to recommend concise research direction tags."""
    prompt = f"""
你是一个科研文献管理助手。请根据论文标题、来源和中文摘要，为这篇论文生成 3 到 6 个中文方向标签。

要求：
1. 标签要短，适合用于个人论文库检索。
2. 优先使用研究方向、任务、方法或应用场景，例如：多模态、AIGC检测、RAG、图像取证、Agent、扩散模型。
3. 不要输出解释，不要 Markdown。
4. 只返回 JSON 数组，例如 ["多模态", "AIGC检测", "图像取证"]。

标题：{title}
来源：{source}
摘要：
{summary[:3000]}
"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        timeout=30,
    )
    content = resp.choices[0].message.content.strip()
    match = re.search(r"\[[\s\S]*\]", content)
    parsed = json.loads(match.group(0) if match else content)
    return normalize_direction_tags(parsed)

def extract_arch_image(pdf_path: Path, doc_id: str) -> str | None:
    """
    通过渲染 PDF 页面提取架构图（同时支持矢量图和位图）：
    1. 在每页的各个文本块中寻找图注（Figure/Fig + 数字 + 架构关键词）
    2. 找到匹配页后，渲染该页为高清位图，并裁剪到图+图注区域
    3. Fallback：渲染第 2~6 页中包含最多图片的那页
    """
    if not FITZ_AVAILABLE:
        print(f"[\033[93mMemory\033[0m] pymupdf 不可用，跳过架构图提取")
        return None

    # 图注中识别架构图的关键词（英文小写，匹配时 text.lower()）
    ARCH_KEYWORDS = [
        # 强信号：明确描述整体架构/框架
        'overall architecture', 'overall framework', 'overall pipeline', 'overall structure',
        'proposed framework', 'proposed architecture',
        'our framework', 'our architecture', 'our model',
        'system overview', 'model overview', 'method overview', 'network architecture',
        'architecture of our', 'framework of our', 'overview of our',
        # 中等信号：在图注块级别仍然准确
        'overview of', 'illustration of', 'the architecture', 'the framework', 'the pipeline',
        'architecture of', 'framework of', 'pipeline of', 'structure of',
        # 中文
        '整体框架', '主体架构', '整体架构', '提出的框架', '网络架构', '模型架构',
    ]
    # 用于提取图号（选图号最小的匹配，因为架构图通常是 Figure 1/2）
    FIG_NUM_RE = re.compile(r'fig(?:ure)?[.\s]*?(\d+)', re.IGNORECASE)
    # 严格匹配：文本块本身以 Fig/Figure/图+数字 开头
    CAPTION_START_RE = re.compile(
        r'^\s*(?:fig(?:ure)?\.?\s*\d+|图\s*\d+)[.:\s：]',
        re.IGNORECASE
    )

    def render_page_region(page, clip_rect=None, scale=2.0) -> bytes:
        """渲染页面（或区域）为 PNG bytes，scale=2 即2x分辨率"""
        mat = fitz.Matrix(scale, scale)
        if clip_rect:
            pix = page.get_pixmap(matrix=mat, clip=clip_rect)
        else:
            pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")

    print(f"[\033[93mMemory\033[0m] 正在逐页扫描图注文本块，寻找主体架构图...")

    try:
        doc = fitz.open(str(pdf_path))
        total_pages = min(20, len(doc))
        page_rect_w = doc[0].rect.width   # 页面宽度（pt）

        # ── Step 1: 逐页、逐文本块匹配图注关键词，收集所有匹配 ──────────────────
        # 最终选图号最小的匹配（架构图通常是 Figure 1 或 Figure 2）
        all_matches = []  # [(fig_num, page_num, cap_x0, cap_y0, cap_x1, cap_y1, kw, blk_preview)]

        for page_num in range(total_pages):
            page = doc[page_num]
            blocks = page.get_text("blocks")
            for blk in blocks:
                if blk[6] != 0:
                    continue
                blk_text = blk[4].strip()
                if not CAPTION_START_RE.match(blk_text):
                    continue
                blk_lower = blk_text.lower()
                for kw in ARCH_KEYWORDS:
                    if kw in blk_lower:
                        # 提取图号
                        m = FIG_NUM_RE.search(blk_text)
                        fig_num = int(m.group(1)) if m else 999
                        all_matches.append((fig_num, page_num, blk[0], blk[1], blk[2], blk[3], kw, blk_text[:100]))
                        break

        # 按图号升序，选最小的（最前面的图 = 最可能是架构图）
        arch_page_num = None
        caption_y0 = None
        matched_kw = None
        if all_matches:
            all_matches.sort(key=lambda x: x[0])
            fig_num, arch_page_num, cap_x0_found, caption_y0, cap_x1_found, cap_y1_found, matched_kw, preview = all_matches[0]
            print(f"[\033[93mMemory\033[0m] 选中 Figure {fig_num}（第{arch_page_num+1}页），关键词: '{matched_kw}'")
            print(f"[\033[93mMemory\033[0m] 图注: {preview}...")
            if len(all_matches) > 1:
                print(f"[\033[93mMemory\033[0m] 其他候选: " + ", ".join(f"Fig{x[0]}(第{x[1]+1}页)" for x in all_matches[1:]))

        # ── Step 2: 渲染匹配页，裁剪到图+图注区域 ───────────────────────────────
        if arch_page_num is not None:
            page = doc[arch_page_num]
            page_h = page.rect.height

            if caption_y0 is not None:
                all_blocks = page.get_text("blocks")
                text_blocks = sorted(
                    [b for b in all_blocks if b[6] == 0],
                    key=lambda b: b[1]
                )

                # 图注底部：直接用匹配时记录的块底部 Y，不要重新计算
                # （重计算会把图注以下所有文字都包进来）
                caption_y1 = cap_y1_found

                # 确定图注所在的栏（Column）边界
                page_w = page.rect.width
                clip_x0 = 0.0
                clip_x1 = page_w
                
                # 简单的双栏检测 heuristic：如果图注偏向一侧，则严格限制在该栏内（以中线为界）
                if cap_x1_found < page_w / 2 + 30:     # 左栏
                    clip_x1 = page_w / 2
                elif cap_x0_found > page_w / 2 - 30:   # 右栏
                    clip_x0 = page_w / 2

                # 1. 寻找正文屏障 (ceiling)，防止向上跨越到上一个段落或上一张图
                ceiling = 0.0
                text_elements = []
                for blk in text_blocks:
                    if blk[3] < caption_y0 - 5:
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
                        if ix0 <= clip_x1 and ix1 >= clip_x0 and iy1 <= caption_y0 + 5:
                            # 过滤超大全页背景图
                            if iy1 - iy0 >= page_h * 0.8:
                                continue
                            # 过滤页面顶部的极小 Logo（如会议标志）
                            if iy0 < 80 and (iy1 - iy0) < 50 and (ix1 - ix0) < 150:
                                continue
                            elements.append((iy0, iy1))
                            
                for d in page.get_drawings():
                    r = d["rect"]
                    if r.x0 <= clip_x1 and r.x1 >= clip_x0 and r.y1 <= caption_y0 + 5:
                        # 过滤极小噪点
                        if r.width < 20 and r.height < 20:
                            continue
                        # 过滤跨越半页的超大背景框
                        if r.height >= page_h * 0.5:
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
                figure_top = caption_y0
                
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
                clip_y1 = max(clip_y0 + 10, caption_y0 - 2)
                clip = fitz.Rect(clip_x0, clip_y0, clip_x1, clip_y1)
                img_bytes = render_page_region(page, clip_rect=clip, scale=2.0)
                print(f"[\033[93mMemory\033[0m] 裁剪: x={clip_x0:.0f}→{clip_x1:.0f}, y={clip_y0:.0f}→{clip_y1:.0f} / 页面高{page_h:.0f}pt")
            else:
                img_bytes = render_page_region(page, scale=2.0)

            out_filename = f"{doc_id}.png"
            out_path = ARCH_IMAGES_DIR / out_filename
            out_path.write_bytes(img_bytes)
            doc.close()
            print(f"[\033[92mMemory\033[0m] 架构图已保存（图注匹配+渲染）: {out_filename} ({len(img_bytes)//1024}KB)")
            return out_filename

        # ── Step 3: Fallback —— 渲染图片最多的页面（第2~7页范围）──────────────
        print(f"[\033[93mMemory\033[0m] 未找到明确图注，回退：渲染图片最多的页...")
        best_page = None
        best_img_count = -1
        for page_num in range(1, min(8, total_pages)):   # 从第2页开始，跳过封面
            count = len(doc[page_num].get_images())
            if count > best_img_count:
                best_img_count = count
                best_page = page_num

        if best_page is None:
            best_page = min(2, total_pages - 1)  # 默认第3页

        page = doc[best_page]
        img_bytes = render_page_region(page, scale=2.0)
        doc.close()

        out_filename = f"{doc_id}.png"
        out_path = ARCH_IMAGES_DIR / out_filename
        out_path.write_bytes(img_bytes)
        print(f"[\033[92mMemory\033[0m] 架构图已保存（渲染第{best_page+1}页，含{best_img_count}个图）: {out_filename} ({len(img_bytes)//1024}KB)")
        return out_filename

    except Exception as e:
        print(f"[\033[31mMemory\033[0m] 架构图提取失败: {type(e).__name__}: {e}")
        return None


def run_ingest_paper(pdf_path: str, custom_title: str = None) -> str:
    """读取 PDF，调用 LLM 总结创新点和架构，并存入向量数据库"""
    path = safe_path(pdf_path)
    if not path.exists() or path.suffix.lower() != '.pdf':
        return f"Error: 找不到 PDF 文件或格式不正确 -> {pdf_path}"
    
    print(f"[\033[93mMemory\033[0m] 正在解析论文 {path.name}，请稍候...")
    
    # 1. 提取文本（通常截取前 10 页就足够涵盖摘要、引言和方法论，避免 token 超限）
    text = ""
    try:
        reader = PdfReader(path)
        for i in range(min(10, len(reader.pages))):
            page_text = reader.pages[i].extract_text() or ""
            text += page_text + "\n"
    except Exception as e:
        return f"Error: 解析 PDF 失败: {e}"

    # 清洗文本：过滤掉 PDF 中数学字体等产生的 Unicode 代理字符（\ud800-\udfff），
    # 这些字符无法被 UTF-8 编码，会导致 JSON 序列化失败。
    text = re.sub(r'[\ud800-\udfff]', '', text)

    # 2. 调用大模型进行后台结构化总结（不污染主对话的上下文）
    prompt = f"""
    你是一个顶级的 AI 领域学术审稿人。请阅读以下论文片段，并给出高度凝练的中文总结。
    
    【极其重要的格式要求】：
    1. 绝对不要输出任何寒暄、自我介绍或开场白（例如“作为审稿人，总结如下：”）。
    2. 绝对不要在标题前面加上“1.”、“2.”或“###”等任何数字或markdown编号。
    3. 你的输出必须且只能直接以“【核心创新点】：”开头。
    
    必须包含且仅包含以下两部分内容：
    【核心创新点】：解决了什么痛点？提出了什么新思想？
    【模型架构设计】：具体的网络结构、模块设计或损失函数创新（如融合头、注意力机制等）。
    
    论文文本：
    {text[:20000]}  # 截断以适应上下文
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        summary = resp.choices[0].message.content
    except Exception as e:
        return f"Error: 大模型总结失败: {e}"

    # 3. 提取主体架构图
    doc_id = custom_title if custom_title else path.stem
    arch_image = extract_arch_image(path, doc_id)
    try:
        direction_tags = generate_paper_direction_tags(doc_id, summary, path.name)
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 自动方向标签生成失败: {e}")
        direction_tags = []
    
    # 4. 向量化入库（metadata 中记录架构图路径）
    metadata = {"source": str(path.name), "type": "research_paper", "summary": summary}
    if direction_tags:
        metadata["direction_tags"] = ",".join(direction_tags)
    if arch_image:
        metadata["arch_image"] = arch_image
    search_document = build_paper_search_document(doc_id, summary, metadata)
    
    paper_collection.upsert(
        documents=[search_document],
        metadatas=[metadata],
        ids=[doc_id]
    )
    
    arch_msg = f"\n\n🏛️ 架构图已提取并保存！" if arch_image else ""
    return f"✅ 论文 [{doc_id}] 已成功总结并永久存入长期记忆库！{arch_msg}\n\n【入库摘要预览】：\n{summary[:300]}..."

def run_search_memory(query: str) -> str:
    """在向量数据库中进行语义检索"""
    print(f"[\033[93mMemory\033[0m] 正在脑海中检索关于 '{query}' 的记忆...")
    
    # 检索最相关的 3 篇文献
    results = paper_collection.query(
        query_texts=[query],
        n_results=3
    )
    
    if not results['documents'] or not results['documents'][0]:
        return "🧠 长期记忆库中没有找到相关文献。"
    
    res_text = "🧠 从长期记忆库中检索到以下高相关度论文：\n"
    for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
        arch_hint = f"\n【架构图】: {meta['arch_image']}" if meta.get('arch_image') else ""
        summary = meta.get("summary", doc)
        comment = f"\n【用户评论】: {meta['comment']}" if meta.get("comment") else ""
        res_text += f"\n📄 【来源文献】: {meta['source']}\n【记忆内容】: {summary}{comment}{arch_hint}\n{'-'*40}"
        
    return res_text
# =====================================================================
