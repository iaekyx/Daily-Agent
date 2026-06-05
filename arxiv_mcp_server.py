#!/usr/bin/env python3
# arxiv_mcp_server.py - 独立的 arXiv 检索 MCP 服务器 (彻底修复缓冲与编码超时版)
from __future__ import annotations

import time
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import ssl
import re
import html
from datetime import datetime

API_BACKOFF_UNTIL = 0

def send_response(msg_id, result=None, error=None):
    """向标准输出打印 JSON-RPC 响应，供 Agent 接收"""
    response = {"jsonrpc": "2.0", "id": msg_id}
    if result is not None:
        response["result"] = result
    if error is not None:
        response["error"] = error
    print(json.dumps(response), flush=True)

def parse_datetime_bound(value: str):
    if not value:
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except Exception:
        return None

def build_search_query(query: str) -> str:
    clean_query = query.strip()
    terms = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", clean_query.replace("-", " "))
        if token.lower() not in {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with"}
    ]
    if len(terms) >= 2:
        return " AND ".join(f"all:{term}" for term in terms)
    return f'all:"{clean_query}"'

def strip_html(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()

def parse_submitted_date(value: str):
    value = strip_html(value)
    match = re.search(r"Submitted\s+(\d{1,2}\s+[A-Za-z]+,\s+\d{4})", value)
    if not match:
        return None, ""
    raw = match.group(1)
    try:
        dt = datetime.strptime(raw, "%d %B, %Y").astimezone()
        return dt, dt.date().isoformat()
    except Exception:
        return None, raw

def format_paper(title: str, published_date: str, published_raw: str, authors: list, link: str, summary: str) -> str:
    return (
        f"标题: {title}\n"
        f"发布日期: {published_date}\n"
        f"发布时间: {published_raw or published_date}\n"
        f"作者: {', '.join(authors)}\n"
        f"链接: {link}\n"
        f"摘要: {summary}\n"
    )

def filter_and_format_papers(candidates: list[dict], result_limit: int, published_after: str = None, published_before: str = None, fallback_latest_on_empty: bool = False) -> str:
    after_dt = parse_datetime_bound(published_after)
    before_dt = parse_datetime_bound(published_before)
    papers = []
    fallback_papers = []

    for item in candidates:
        paper_text = format_paper(
            item.get("title", ""),
            item.get("published_date", ""),
            item.get("published_raw", ""),
            item.get("authors", []),
            item.get("link", ""),
            item.get("summary", ""),
        )
        if len(fallback_papers) < 3:
            fallback_papers.append(paper_text)

        published_dt = item.get("published_dt")
        if after_dt and published_dt and published_dt <= after_dt:
            continue
        if before_dt and published_dt and published_dt > before_dt:
            continue
        papers.append(paper_text)
        if len(papers) >= result_limit:
            break

    if not papers:
        if fallback_latest_on_empty and fallback_papers:
            return (
                f"发布时间在 {published_after or '-∞'} 到 {published_before or '+∞'} 之间没有检索到新论文。\n"
                "以下返回该关键词下最新的 3 篇论文作为参考：\n\n"
                + "\n---\n".join(fallback_papers)
            )
        if published_after or published_before:
            return f"未找到发布时间在 {published_after or '-∞'} 到 {published_before or '+∞'} 之间的相关论文。"
        return "未找到相关论文。"
    return "\n---\n".join(papers)

def parse_api_entries(xml_data: bytes) -> list[dict]:
    root = ET.fromstring(xml_data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    candidates = []
    for entry in root.findall('atom:entry', ns):
        title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
        summary = entry.find('atom:summary', ns).text.replace('\n', ' ').strip()
        link = entry.find('atom:id', ns).text
        authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
        published_raw = entry.find('atom:published', ns).text
        candidates.append({
            "title": title,
            "summary": summary,
            "link": link,
            "authors": authors,
            "published_raw": published_raw,
            "published_date": published_raw[:10],
            "published_dt": parse_datetime_bound(published_raw),
        })
    return candidates

def search_arxiv_html_fallback(query: str, max_results: int, published_after: str = None, published_before: str = None, fallback_latest_on_empty: bool = False) -> str:
    """Fallback parser for arxiv.org/search when export API is rate-limited or slow."""
    size = 50 if (published_after or published_before or max_results > 25) else 25
    params = urllib.parse.urlencode({
        "query": query,
        "searchtype": "all",
        "abstracts": "show",
        "order": "-announced_date_first",
        "size": str(size),
    })
    url = f"https://arxiv.org/search/?{params}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    ssl_context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=35, context=ssl_context) as response:
        page = response.read().decode("utf-8", errors="ignore")

    candidates = []
    for block in re.findall(r'<li class="arxiv-result">([\s\S]*?)</li>\s*(?=<li class="arxiv-result">|</ol>)', page):
        link_match = re.search(r'<p class="list-title[\s\S]*?<a href="([^"]+)">arXiv:[^<]+</a>', block)
        title_match = re.search(r'<p class="title is-5 mathjax">([\s\S]*?)</p>', block)
        authors_match = re.search(r'<p class="authors">([\s\S]*?)</p>', block)
        abstract_match = re.search(
            r'<span class="abstract-full[\s\S]*?style="display:\s*none;">([\s\S]*?)<a class="is-size-7"',
            block,
        )
        if not abstract_match:
            abstract_match = re.search(r'<p class="abstract mathjax">([\s\S]*?)</p>', block)
        submitted_match = re.search(r'<p class="is-size-7">([\s\S]*?Submitted[\s\S]*?)</p>', block)

        title = strip_html(title_match.group(1)) if title_match else ""
        link = link_match.group(1) if link_match else ""
        authors = re.findall(r'<a href="/search/\?searchtype=author[^"]*">([\s\S]*?)</a>', authors_match.group(1) if authors_match else "")
        authors = [strip_html(author) for author in authors]
        summary = strip_html(abstract_match.group(1)).replace("Abstract :", "").replace("Abstract:", "").strip() if abstract_match else ""
        published_dt, published_date = parse_submitted_date(submitted_match.group(1) if submitted_match else "")
        if title:
            candidates.append({
                "title": title,
                "summary": summary,
                "link": link,
                "authors": authors,
                "published_raw": published_date,
                "published_date": published_date,
                "published_dt": published_dt,
            })

    if not candidates:
        return "【检索失败】arXiv API 不可用，网页降级检索也没有解析到结果。"

    result = filter_and_format_papers(candidates, max_results, published_after, published_before, fallback_latest_on_empty)
    return "【提示】arXiv API 当前不可用，以下结果来自 arXiv 搜索网页降级检索。\n\n" + result

def search_arxiv(query: str, max_results: int = None, published_after: str = None, published_before: str = None, fallback_latest_on_empty: bool = False) -> str:
    """调用 arXiv 官方 API 并解析 XML 结果 (修复二次编码与重定向问题)"""
    global API_BACKOFF_UNTIL
    # 1. 清理两端空格
    clean_query = query.strip()
    after_dt = parse_datetime_bound(published_after)
    before_dt = parse_datetime_bound(published_before)
    result_limit = max_results or (50 if (after_dt or before_dt) else 3)
    fetch_limit = min(max(result_limit * 4, 20), 100) if (after_dt or before_dt) else result_limit
    
    # 2. 将自然语言关键词转成更宽松的 AND 检索。
    # 精确短语会漏掉 Detector/Detectors、AI-generated imagery 等常见变体。
    formatted_query = build_search_query(clean_query)
    encoded_query = urllib.parse.quote(formatted_query)
    
    # 3. 明确使用 https 协议，防止 301 重定向在代理环境中卡死
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={fetch_limit}&sortBy=submittedDate&sortOrder=descending"

    if time.time() < API_BACKOFF_UNTIL:
        try:
            return search_arxiv_html_fallback(clean_query, result_limit, published_after, published_before, fallback_latest_on_empty)
        except Exception as fallback_error:
            return f"请求 arXiv API 处于临时退避期；网页降级检索失败: {fallback_error}"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        ssl_context = ssl._create_unverified_context()
        
        # API 近期经常限流/卡住。保持较短超时，失败后自动走网页搜索降级。
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
            xml_data = response.read()
        
        candidates = parse_api_entries(xml_data)
        return filter_and_format_papers(candidates, result_limit, published_after, published_before, fallback_latest_on_empty)
        
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[\033[33marXiv 警告\033[0m] 请求过快被限流 (Error 429)！", file=sys.stderr)
            API_BACKOFF_UNTIL = time.time() + 300
            try:
                return search_arxiv_html_fallback(clean_query, result_limit, published_after, published_before, fallback_latest_on_empty)
            except Exception as fallback_error:
                return f"【检索失败】arXiv API 触发 429 限流，网页降级检索也失败: {fallback_error}。请等待 3-5 分钟后再试，或检查代理分流规则。"
        else:
            print(f"[\033[31marXiv 报错\033[0m] HTTP Error {e.code}", file=sys.stderr)
            try:
                return search_arxiv_html_fallback(clean_query, result_limit, published_after, published_before, fallback_latest_on_empty)
            except Exception:
                return f"请求 arXiv 时发生 HTTP 错误: {e.code}"
    except Exception as e:
        print(f"[\033[31marXiv 错误\033[0m] {str(e)}", file=sys.stderr)
        if "timed out" in str(e).lower() or "urlopen error" in str(e).lower():
            API_BACKOFF_UNTIL = time.time() + 300
        try:
            return search_arxiv_html_fallback(clean_query, result_limit, published_after, published_before, fallback_latest_on_empty)
        except Exception as fallback_error:
            return f"请求 arXiv API 失败: {str(e)}；网页降级检索也失败: {fallback_error}"

def main():
    # 🌟 核心修复：严禁使用 for line in sys.stdin，改用 readline() 规避管道块缓冲卡死
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # 管道关闭则退出
            
        if not line.strip():
            continue
            
        try:
            req = json.loads(line)
            method = req.get("method")
            msg_id = req.get("id")
            
            # 1. 握手初始化
            if method == "initialize":
                send_response(msg_id, result={
                    "capabilities": {},
                    "serverInfo": {"name": "arxiv-searcher", "version": "1.2"}
                })
            
            # 2. 汇报技能
            elif method == "tools/list":
                send_response(msg_id, result={
                    "tools": [{
                        "name": "search_arxiv",
                        "description": "在 arXiv 上搜索最新的学术论文（默认已按最新提交时间倒序排列）。可通过 max_results 控制数量；如果需要增量日报，请传入 published_after 和 published_before，工具会在程序层只返回该时间区间内发布的论文。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string", 
                                    "description": "搜索关键词，例如 'AI-Generated Image Detection'"
                                },
                                "max_results": {
                                    "type": "integer", 
                                    "description": "返回的论文数量。普通搜索默认 3；带时间区间过滤时默认 50"
                                },
                                "published_after": {
                                    "type": "string",
                                    "description": "只返回此时间之后发布的论文，ISO 格式，例如 2026-05-29T09:00:00+08:00"
                                },
                                "published_before": {
                                    "type": "string",
                                    "description": "只返回此时间之前或等于此时间发布的论文，ISO 格式，例如 2026-06-01T09:00:00+08:00"
                                },
                                "fallback_latest_on_empty": {
                                    "type": "boolean",
                                    "description": "当时间区间内没有新论文时，是否返回该关键词下最新的 3 篇论文作为参考。每日简报建议设为 true"
                                }
                            },
                            "required": ["query"]
                        }
                    }]
                })
            
            # 3. 执行具体工具调用
            elif method == "tools/call":
                params = req.get("params", {})
                if params.get("name") == "search_arxiv":
                    args = params.get("arguments", {})
                    query = args.get("query", "")
                    max_res = args.get("max_results")
                    published_after = args.get("published_after")
                    published_before = args.get("published_before")
                    fallback_latest_on_empty = bool(args.get("fallback_latest_on_empty", False))
                    
                    result_text = search_arxiv(query, max_res, published_after, published_before, fallback_latest_on_empty)
                    
                    send_response(msg_id, result={
                        "content": [{"type": "text", "text": result_text}]
                    })
                else:
                    send_response(msg_id, error={"code": -32601, "message": "Tool not found"})
                    
        except Exception as e:
            # 打印到 stderr 供调试，防止进程崩溃
            print(f"[\033[31mMCP 核心异常\033[0m] {e}", file=sys.stderr)
            pass

if __name__ == "__main__":
    main()
