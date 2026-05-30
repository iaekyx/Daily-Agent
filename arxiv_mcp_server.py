#!/usr/bin/env python3
# arxiv_mcp_server.py - 独立的 arXiv 检索 MCP 服务器 (彻底修复缓冲与编码超时版)
import time
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import ssl

def send_response(msg_id, result=None, error=None):
    """向标准输出打印 JSON-RPC 响应，供 Agent 接收"""
    response = {"jsonrpc": "2.0", "id": msg_id}
    if result is not None:
        response["result"] = result
    if error is not None:
        response["error"] = error
    print(json.dumps(response), flush=True)

def search_arxiv(query: str, max_results: int = 3) -> str:
    """调用 arXiv 官方 API 并解析 XML 结果 (修复二次编码与重定向问题)"""
    # 1. 清理两端空格
    clean_query = query.strip()
    
    # 2. 采用 arXiv 官方推荐的精确短语匹配语法：all:"你的关键词"
    # 这样 urllib 会把空格安全地转为 %20，而不是带有歧义且容易引发挂起的 %2B
    formatted_query = f'all:"{clean_query}"'
    encoded_query = urllib.parse.quote(formatted_query)
    
    # 3. 明确使用 https 协议，防止 301 重定向在代理环境中卡死
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        ssl_context = ssl._create_unverified_context()
        
        # 将 timeout 延长至 15 秒，给慢速网络留出足够的读取时间，防止频繁发生 read operation timed out
        with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        papers = []
        for entry in root.findall('atom:entry', ns):
            title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
            summary = entry.find('atom:summary', ns).text.replace('\n', ' ').strip()
            link = entry.find('atom:id', ns).text
            authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
            
            published_date = entry.find('atom:published', ns).text[:10] 
            papers.append(f"标题: {title}\n发布日期: {published_date}\n作者: {', '.join(authors)}\n链接: {link}\n摘要: {summary}\n")
            
        if not papers:
            return "未找到相关论文。"
        return "\n---\n".join(papers)
        
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[\033[33marXiv 警告\033[0m] 请求过快被限流 (Error 429)！", file=sys.stderr)
            return "【检索失败】由于近期检索过于频繁，触发了 arXiv 官方的 429 限流保护。请检查您的代理分流规则，或等待 3-5 分钟后再试。"
        else:
            print(f"[\033[31marXiv 报错\033[0m] HTTP Error {e.code}", file=sys.stderr)
            return f"请求 arXiv 时发生 HTTP 错误: {e.code}"
    except Exception as e:
        print(f"[\033[31marXiv 错误\033[0m] {str(e)}", file=sys.stderr)
        return f"请求 arXiv 时发生未知错误或超时: {str(e)}"

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
                        "description": "在 arXiv 上搜索最新的学术论文（默认已按最新提交时间倒序排列）。可通过 max_results 控制数量。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string", 
                                    "description": "搜索关键词，例如 'AI-Generated Image Detection'"
                                },
                                "max_results": {
                                    "type": "integer", 
                                    "description": "返回的论文数量，默认 3"
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
                    max_res = args.get("max_results", 3)
                    
                    result_text = search_arxiv(query, max_res)
                    
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