#!/usr/bin/env python3
import sys
import json
import os
import urllib.request
import urllib.error
import urllib.parse
import ssl
from datetime import datetime

DB_FILE = "favorites.json"
MD_FILE = "favorites.md"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.loads(f.read())
    return []

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    render_markdown(data)

def render_markdown(data):
    """防弹版 Markdown 渲染器"""
    try:
        lines = ["# 个人科研论文收藏夹\n\n> 🤖 由 Agent 自动维护与监控仓库更新\n\n"]
        for item in data:
            title = item.get('title', '未知标题')
            link = item.get('link', '无链接')
            has_repo = item.get('has_repo', False)
            repo = item.get('repo', '')
            desc = item.get('description', '暂无介绍')
            date_str = item.get('collected_at', '未知时间')
            pushed_at = item.get('last_pushed_at', '')

            repo_status = "✅ 已找到" if has_repo else "❌ 暂无"
            repo_link = f" ({repo})" if repo else ""
            
            lines.append(f"### {title}")
            lines.append(f"- **GitHub 仓库**: {repo_status}{repo_link}")
            lines.append(f"- **论文链接**: {link}")
            lines.append(f"- **收藏时间**: {date_str}")
            if pushed_at:
                lines.append(f"- **最后代码提交**: {str(pushed_at)[:10]}")
            lines.append(f"- **研究简述**: {desc}")
            lines.append("\n---\n")
            
        with open(MD_FILE, "w", encoding="utf-8") as f:
            f.writelines([line + "\n" for line in lines])
    except Exception as e:
        print(f"Markdown 渲染失败: {e}", file=sys.stderr)

def github_request(url):
    ssl_context = ssl._create_unverified_context()
    headers = {'User-Agent': 'Mozilla/5.0'}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers['Authorization'] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
            return json.loads(response.read())
    except Exception as e:
        print(f"[\033[31mGitHub API 请求失败\033[0m] {e}", file=sys.stderr)
        return None

import time # 🚨 记得在文件最上方加上这个！

def is_author_repo(name):
    """纯净排雷器：在本地过滤垃圾仓库"""
    name = name.lower()
    blacklist = ['awesome', 'list', 'papers', 'reading', 'survey', 'collection', 'archive']
    for word in blacklist:
        if word in name:
            return False
    return True

# ================= 核心功能 1：批量获取候选仓库 =================
def get_missing_repo_candidates():
    """扫描所有无仓库的论文，为每篇检索前 8 个候选仓库"""
    db = load_db()
    results = []
    
    for item in db:
        if not item.get('has_repo'):
            title = item.get('title', '')
            link = item.get('link', '')
            arxiv_id = link.split("arxiv.org/abs/")[-1].split("v")[0] if "arxiv.org/abs/" in link else ""
            
            raw_candidates = []
            
            import time # 确保有 time
            
            # 💡 策略 A：极简 arXiv ID 搜索 (绝对不加任何减号)
            if arxiv_id:
                query_id = urllib.parse.quote(arxiv_id)
                url_id = f"https://api.github.com/search/repositories?q={query_id}&sort=stars&per_page=10"
                res_id = github_request(url_id)
                if res_id and res_id.get('items'):
                    raw_candidates.extend(res_id['items'])
                time.sleep(2)

            # 💡 策略 B：主标题的【双引号精确匹配】
            # 直接提取冒号前面的完整主标题
            short_title = title.split(":")[0].strip()
            
            # 🚨 核心修复：用双引号把整个主标题包起来！
            # 这会强迫 GitHub 搜索引擎：必须一字不差、连顺序都不能错地匹配这句话！
            query_title = urllib.parse.quote(f'"{short_title}"')
            url_title = f"https://api.github.com/search/repositories?q={query_title}&sort=stars&per_page=10"
            
            res_title = github_request(url_title)
            if res_title and res_title.get('items'):
                raw_candidates.extend(res_title['items'])
            time.sleep(2)

            # 🧹 数据清洗、去重 与 本地排雷
            seen_urls = set()
            final_candidates = []
            for c in raw_candidates:
                url = c.get("html_url")
                name = c.get("full_name")
                
                # 🚨 核心魔法：在这里调用 Python 本地排雷器！
                if url not in seen_urls and is_author_repo(name):
                    seen_urls.add(url)
                    final_candidates.append({
                        "name": name,
                        "url": url,
                        "stars": c.get("stargazers_count"),
                        "description": c.get("description", "无简介")
                    })
                    if len(final_candidates) >= 8:
                        break
            
            results.append({
                "title": title,
                "candidates_found": len(final_candidates),
                "candidates": final_candidates
            })
            
    if not results:
        return "数据库中所有论文都已绑定仓库，目前无需检索。"
    return json.dumps(results, ensure_ascii=False, indent=2)

# ================= 核心功能 2：检查已有仓库的更新 =================
def check_repo_updates():
    """仅检查 has_repo 为 True 的项目，看是否有新的 commit"""
    db = load_db()
    render_markdown(db) # 强制同步页面
    report = []
    updated = False

    for item in db:
        if item.get('has_repo') and item.get('repo') and "github.com" in item['repo']:
            api_url = item['repo'].replace("github.com/", "api.github.com/repos/")
            res = github_request(api_url)
            
            if res and res.get('pushed_at'):
                old_push = item.get('last_pushed_at', '')
                new_push = res['pushed_at']
                is_valid_date = old_push and len(old_push) >= 10 and old_push[0].isdigit()
                if is_valid_date and new_push > old_push:
                    report.append(f"🔄 **代码更新**：《{item['title'][:20]}...》有新提交 ({new_push[:10]})！")
                item['last_pushed_at'] = new_push
                updated = True

    if updated:
        save_db(db)
        
    if not report:
        return "巡检完毕，已有仓库均无新代码提交。"
    return "巡检完毕，动态如下：\n" + "\n".join(report)

# ================= 核心功能 3：工具操作 (保存 & 更新) =================
def update_paper_repo(title, repo_url):
    db = load_db()
    for item in db:
        if item.get("title") == title:
            item["has_repo"] = True
            item["repo"] = repo_url
            item["last_pushed_at"] = "已绑定 (Agent)"
            save_db(db)
            return f"✅ 成功绑定！官方仓库 {repo_url} 已写入论文《{title[:15]}...》。"
    return "❌ 更新失败：未在数据库中找到该论文。"

def send_response(msg_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": msg_id}
    if result is not None: response["result"] = result
    if error is not None: response["error"] = error
    print(json.dumps(response), flush=True)

def main():
    if not os.path.exists(DB_FILE): save_db([])
        
    for line in sys.stdin:
        if not line.strip(): continue
        try:
            req = json.loads(line)
            method = req.get("method")
            msg_id = req.get("id")

            if method == "initialize":
                send_response(msg_id, result={"capabilities": {}, "serverInfo": {"name": "collector", "version": "5.0"}})
            elif method == "tools/list":
                send_response(msg_id, result={
                    "tools": [
                        {
                            "name": "get_missing_repo_candidates",
                            "description": "扫描收藏夹中【没有仓库】的论文，一次性获取每篇论文在 GitHub 上的前 8 名嫌疑仓库。\n\n【LLM 裁判指令】：收到批量候选人后，你必须逐一甄别。排除 Unofficial, Reproduction 以及重名但内容/作者完全无关的仓库（例如重名的毕业设计、其他工具项目）。判定官方仓库的硬性标准：1. 仓库 README/描述中必须明确提及该论文标题或作者。2. 仓库所有者/贡献者应属于论文作者，若完全无关则严禁绑定！如果无法完全确认官方身份，宁可不绑定，向用户请示。",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "check_repo_updates",
                            "description": "扫描收藏夹中【已有仓库】的论文，检查是否有新的代码提交(push)。",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "save_article",
                            "description": "将新论文存入收藏夹数据库。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"}, "link": {"type": "string"},
                                    "has_repo": {"type": "boolean"}, "repo": {"type": "string"},
                                    "description": {"type": "string"}
                                },
                                "required": ["title", "link", "has_repo"]
                            }
                        },
                        {
                            "name": "update_paper_repo",
                            "description": "判定出真正的官方仓库后，将链接写入对应论文。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string", "description": "原论文完整标题"},
                                    "repo_url": {"type": "string", "description": "官方 GitHub 链接"}
                                },
                                "required": ["title", "repo_url"]
                            }
                        }
                    ]
                })
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name")
                args = params.get("arguments", {})
                
                if name == "get_missing_repo_candidates":
                    send_response(msg_id, result={"content": [{"type": "text", "text": get_missing_repo_candidates()}]})
                elif name == "check_repo_updates":
                    send_response(msg_id, result={"content": [{"type": "text", "text": check_repo_updates()}]})
                elif name == "save_article":
                    db = load_db()
                    db.append({
                        "title": args.get("title"), "link": args.get("link"),
                        "has_repo": args.get("has_repo"), "repo": args.get("repo", ""),
                        "description": args.get("description", ""),
                        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "last_pushed_at": ""
                    })
                    save_db(db)
                    send_response(msg_id, result={"content": [{"type": "text", "text": "成功存入数据库。"}]})
                elif name == "update_paper_repo":
                    send_response(msg_id, result={"content": [{"type": "text", "text": update_paper_repo(args.get("title"), args.get("repo_url"))}]})
                    
        except Exception as e:
            print(f"RPC 错误: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()