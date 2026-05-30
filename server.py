import os
os.environ["NO_PROXY"] = "dashscope.aliyuncs.com,aliyuncs.com,aliyun.com,localhost,127.0.0.1"
os.environ["no_proxy"] = "dashscope.aliyuncs.com,aliyuncs.com,aliyun.com,localhost,127.0.0.1"
import json
import asyncio
import threading
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import agent # Import the agent module
from agent_runtime.memory import build_paper_search_document, generate_paper_direction_tags

app = FastAPI()

allowed_origins = os.environ.get(
    "DAILY_AGENT_ALLOWED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKDIR = Path.cwd()
READING_QUEUE_FILE = WORKDIR / "reading_queue.json"
MEALS_FILE = WORKDIR / "meals.json"
FOOD_RULES_FILE = WORKDIR / "data" / "food_rules.json"

# Serve static frontend files
frontend_dir = WORKDIR / "frontend"
frontend_dir.mkdir(exist_ok=True)
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

# Serve arch images
arch_images_dir = WORKDIR / "vector_db" / "arch_images"
arch_images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/arch_images", StaticFiles(directory=str(arch_images_dir)), name="arch_images")

@app.get("/")
def read_root():
    return FileResponse(frontend_dir / "index.html")

# State for web socket permission loop
class AgentSession:
    def __init__(self):
        self.permission_event = threading.Event()
        self.permission_answer = False
        self.websocket = None
        self.main_loop = None # Main thread event loop
        self.messages = [] # chat history

session = AgentSession()

def safe_send_text(req: dict):
    if session.websocket and session.main_loop:
        try:
            coro = session.websocket.send_text(json.dumps(req))
            asyncio.run_coroutine_threadsafe(coro, session.main_loop)
        except Exception as e:
            print(f"Error in safe_send_text: {e}")

def web_ask_user_callback(intent: dict, tool_input: dict) -> bool:
    if session.websocket:
        # Send request to frontend
        req = {
            "type": "ask_permission",
            "intent": intent,
            "tool_input": tool_input
        }
        safe_send_text(req)
        # Wait for answer
        session.permission_event.clear()
        session.permission_event.wait()
        return session.permission_answer
    return False

def web_print_callback(log_str: str):
    req = {
        "type": "log",
        "content": log_str
    }
    safe_send_text(req)

def web_finish_callback(content: str):
    if content:
        req = {
            "type": "message",
            "content": content
        }
        safe_send_text(req)

def web_stream_start_callback():
    safe_send_text({"type": "message_start"})

def web_stream_delta_callback(content: str):
    if content:
        safe_send_text({"type": "message_delta", "content": content})

def web_stream_end_callback():
    safe_send_text({"type": "message_end"})

# Inject callbacks into agent module
agent.permission_gate.web_ask_user_callback = web_ask_user_callback
agent.web_print_callback = web_print_callback
agent.web_finish_callback = web_finish_callback
agent.web_stream_start_callback = web_stream_start_callback
agent.web_stream_delta_callback = web_stream_delta_callback
agent.web_stream_end_callback = web_stream_end_callback

agent.init_agent()

@app.on_event("shutdown")
def shutdown_event():
    agent.cleanup_agent()

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session.websocket = websocket
    session.main_loop = asyncio.get_running_loop() # Capture main thread loop!
    await websocket.send_text(json.dumps({"type": "status", "content": "WebSocket connected"}))
    
    # Check and run daily task on first connection of the day
    def run_daily_task():
        try:
            today = agent.datetime.date.today().isoformat()
            config = {"last_run": "", "keywords": ["AI-Generated Image Detection", "Agentic Workflow"]}
            
            if agent.STATUS_FILE.exists():
                try:
                    config = json.loads(agent.STATUS_FILE.read_text())
                except: pass

            if config.get("last_run") != today:
                report_date = (agent.datetime.date.today() - agent.datetime.timedelta(days=1)).isoformat()
                try:
                    meal_analysis_context = build_meal_analysis(3)
                    meal_records_context = [
                        enriched_meal(meal)
                        for meal in read_json_file(MEALS_FILE, [])
                        if meal.get("date", "") >= (date.today() - timedelta(days=2)).isoformat()
                    ]
                except Exception as e:
                    print(f"Error preparing meal briefing context: {e}")
                    meal_analysis_context = {"total_meals": 0, "suggestions": ["暂无可用饮食记录，请给出均衡饮食建议。"]}
                    meal_records_context = []

                meal_context_json = json.dumps({
                    "recent_records": meal_records_context,
                    "analysis": meal_analysis_context,
                }, ensure_ascii=False)

                # Notify frontend that we are performing daily check
                web_print_callback("📅 检测到今日首次启动，正在执行自动巡检并生成每日简报...")
                
                query = f"""
今天是 {today}，请帮我生成一份【每日简报】。
论文检索窗口固定为昨日：{report_date}。不要检索今天的论文，因为今天发布尚不完整。

以下是后端已经预先整理好的近三天饮食上下文，生成饮食建议时必须优先使用它，不要忽略：
```json
{meal_context_json}
```

你需要完成以下 5 个**完全独立**的子任务：
1. 检查收藏夹中【已有代码】仓库的最新动态 (check_repo_updates)。
2. 为收藏夹中【无代码】的论文寻找并绑定官方仓库 (get_missing_repo_candidates)。
3. 检索关于 '{" 和 ".join(config.get("keywords", []))}' 的昨日论文 (search_arxiv，严查发布日期必须等于 {report_date}，必须精准且严格使用这些指定关键词作为搜索 query，绝对禁止盲目套用 'Large Language Models' 等无关通用占位符！)。
4. 生成【本周待读提醒】：读取 reading_queue.json，概括本周待读论文数量和最值得优先读的 1-3 篇；如果没有待读，就提示从文献记忆库添加。
5. 生成【今日饮食建议】：根据上面的近三天饮食上下文，结合今天日期 {today} 给出今天早餐/午餐/晚餐的简短建议。建议要具体、可执行，并尽量弥补近期饮食中的不足；如果上下文没有历史记录，就给出均衡饮食建议。

【强制执行策略】：
这五个任务互相没有依赖。作为高阶 Manager，你**绝不能**自己按顺序挨个执行！
你必须立刻调用 `delegate_tasks` 工具，将这 5 个任务包装成独立的 Task，派发给 5 个专属下属 Agent **并行执行**！

当所有下属向你汇报完成后，请你整合他们的结果，用 Markdown 严格按以下固定结构输出【每日简报】：
## 每日简报｜{today}
### 📅 今日概览
(用 2-4 条 bullet 简述今天最重要的信息：学术动态、待读、饮食建议。)
### 🔄 收藏夹动态
(列出仓库代码更新，以及今天新找到并绑定的仓库)
### 📄 arXiv 新论文
(只列出发布日期为 {report_date} 的论文标题和链接，没有就写“昨日无新论文”)
### 📖 本周待读提醒
(列出待读数量和建议优先阅读的论文；没有待读就提示从文献记忆库添加)
### 🍱 今日饮食建议
(这一节必须输出，不能省略。基于近三天饮食上下文，给出今天怎么吃的具体建议；如果没有饮食记录，也必须输出均衡建议。不要写医疗诊断，不要夸大营养结论)
"""
                auto_history = [{"role": "user", "content": query}]
                agent.agent_loop(auto_history)
                
                # Append to session messages so chat has context
                session.messages.extend(auto_history)
                
                # Mark as run
                config["last_run"] = today
                agent.STATUS_FILE.write_text(json.dumps(config, indent=2))
                
                # Notify done
                safe_send_text({"type": "done"})
        except Exception as e:
            print(f"Error in background daily task: {e}")
            
    threading.Thread(target=run_daily_task, daemon=True).start()
    
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            if payload.get("type") == "chat":
                user_msg = payload.get("content")
                session.messages.append({"role": "user", "content": user_msg})
                # Run agent loop in thread
                def run_agent():
                    try:
                        agent.agent_loop(session.messages)
                        # Notify frontend that run is complete
                        safe_send_text({"type": "done"})
                    except Exception as e:
                        import traceback
                        err_msg = traceback.format_exc()
                        print(f"Agent error details:\n{err_msg}")
                        safe_send_text({"type": "error", "content": str(e)})
                
                threading.Thread(target=run_agent).start()
                
            elif payload.get("type") == "permission_answer":
                session.permission_answer = payload.get("answer")
                session.permission_event.set()
                
    except WebSocketDisconnect:
        session.websocket = None
    except Exception as e:
        import traceback
        print(f"WebSocket handler error:\n{traceback.format_exc()}")
        session.websocket = None
        try:
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass

@app.get("/api/favorites")
def get_favorites():
    try:
        data = json.loads((WORKDIR / "favorites.json").read_text())
        return JSONResponse(data)
    except:
        return JSONResponse([])

def render_favorites_markdown(data):
    lines = ["# 个人科研论文收藏夹\n\n> 🤖 由 Agent 自动维护与监控仓库更新\n\n"]
    for item in data:
        title = item.get("title", "未知标题")
        link = item.get("link", "无链接")
        has_repo = item.get("has_repo", False)
        repo = item.get("repo", "")
        desc = item.get("description", "暂无介绍")
        date_str = item.get("collected_at", "未知时间")
        pushed_at = item.get("last_pushed_at", "")

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

    (WORKDIR / "favorites.md").write_text("\n".join(lines), encoding="utf-8")

@app.delete("/api/favorites")
async def delete_favorite(request: Request):
    try:
        payload = await request.json()
        title = payload.get("title")
        link = payload.get("link")
        if not title and not link:
            return JSONResponse({"status": "error", "message": "title or link is required"}, status_code=400)

        favorites_path = WORKDIR / "favorites.json"
        data = json.loads(favorites_path.read_text()) if favorites_path.exists() else []
        original_len = len(data)
        data = [
            item for item in data
            if not (
                (link and item.get("link") == link)
                or (not link and title and item.get("title") == title)
            )
        ]

        if len(data) == original_len:
            return JSONResponse({"status": "error", "message": "Favorite not found"}, status_code=404)

        favorites_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        render_favorites_markdown(data)
        return JSONResponse({"status": "ok", "deleted": original_len - len(data)})
    except Exception as e:
        print(f"Error deleting favorite: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

def read_json_file(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading {path.name}: {e}")
    return default

def write_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def normalize_meal_type(value: str) -> str:
    mapping = {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "snack": "夜宵",
        "night": "夜宵",
        "other": "其他",
    }
    text = str(value or "").strip()
    return mapping.get(text.lower(), text or "其他")

def load_food_rules():
    return read_json_file(FOOD_RULES_FILE, {"foods": {}, "aliases": {}})

MEAL_TOKEN_STOPWORDS = {
    "今天", "昨天", "前天", "早餐", "午餐", "晚餐", "夜宵", "早饭", "午饭", "晚饭",
    "吃了", "吃的", "我吃了", "加了", "一个", "一杯", "一份", "一点", "少许",
    "和", "还有", "以及", "外卖", "堂食"
}

def meal_tokens(content: str) -> list:
    text = str(content or "").strip()
    text = re.sub(r"(今天|昨天|前天)?(早上|中午|晚上)?(我)?(吃了|吃的|点了|喝了)", " ", text)
    raw_tokens = re.split(r"[\s,.;:!?，。！？；：、/|+和及]+", text)
    tokens = []
    for token in raw_tokens:
        cleaned = token.strip("()（）[]【】{}<>《》\"'“”‘’")
        if not cleaned or cleaned in MEAL_TOKEN_STOPWORDS:
            continue
        if re.fullmatch(r"\d+", cleaned):
            continue
        tokens.append(cleaned)
    return tokens

def analyze_meal_content(content: str) -> dict:
    rules = load_food_rules()
    foods = rules.get("foods", {})
    aliases = rules.get("aliases", {})
    haystack = (content or "").lower()
    matched = []
    seen = set()

    def add_match(name):
        if name in foods and name not in seen:
            rule = foods[name]
            matched.append({
                "name": name,
                "category": rule.get("category", ""),
                "tags": rule.get("tags", []),
                "health_score": rule.get("health_score", 0),
                "protein": rule.get("protein", "none"),
                "carb": rule.get("carb", "none"),
                "fat": rule.get("fat", "none"),
                "fiber": rule.get("fiber", "none"),
                "sugar": rule.get("sugar", "none"),
                "salt": rule.get("salt", "none"),
            })
            seen.add(name)

    for alias, canonical in aliases.items():
        if alias and alias.lower() in haystack:
            add_match(canonical)

    for name in foods:
        if name and name.lower() in haystack:
            add_match(name)

    unknown_foods = []
    for token in meal_tokens(content):
        token_lower = token.lower()
        matched_in_token = False
        for name in seen:
            if name and name.lower() in token_lower:
                matched_in_token = True
                break
        if not matched_in_token:
            for alias, canonical in aliases.items():
                if alias and alias.lower() in token_lower and canonical in seen:
                    matched_in_token = True
                    break
        if not matched_in_token and token not in unknown_foods:
            unknown_foods.append(token)

    tags = sorted({tag for item in matched for tag in item.get("tags", [])})
    categories = sorted({item.get("category") for item in matched if item.get("category")})
    score = round(sum(item.get("health_score", 0) for item in matched) / len(matched), 2) if matched else None

    return {
        "matched_foods": matched,
        "matched_names": [item["name"] for item in matched],
        "tags": tags,
        "categories": categories,
        "health_score": score,
        "unknown_foods": unknown_foods,
        "confidence": "medium" if matched else "low",
    }

def enriched_meal(meal: dict) -> dict:
    item = dict(meal)
    item["type"] = normalize_meal_type(item.get("type"))
    analysis = item.get("analysis")
    if not analysis or "unknown_foods" not in analysis:
        analysis = analyze_meal_content(item.get("content", ""))
    item["analysis"] = analysis
    return item

def find_meal_index(meals: list, meal_date: str, meal_type: str):
    normalized_type = normalize_meal_type(meal_type)
    return next(
        (i for i, item in enumerate(meals)
         if item.get("date") == meal_date and normalize_meal_type(item.get("type")) == normalized_type),
        None
    )

def meal_level_counts(meals: list) -> dict:
    counts = {
        "protein_high": 0,
        "fiber_high": 0,
        "sugar_high": 0,
        "salt_high": 0,
        "fat_high": 0,
        "takeout": 0,
        "fried": 0,
        "vegetable": 0,
        "unknown": 0,
    }
    for meal in meals:
        analysis = meal.get("analysis") or {}
        matched = analysis.get("matched_foods", [])
        tags = set(analysis.get("tags", []))
        categories = set(analysis.get("categories", []))
        unknown_foods = analysis.get("unknown_foods", [])
        if not matched or unknown_foods:
            counts["unknown"] += 1
        if any(item.get("protein") == "high" for item in matched):
            counts["protein_high"] += 1
        if any(item.get("fiber") == "high" for item in matched):
            counts["fiber_high"] += 1
        if any(item.get("sugar") == "high" for item in matched):
            counts["sugar_high"] += 1
        if any(item.get("salt") == "high" for item in matched):
            counts["salt_high"] += 1
        if any(item.get("fat") == "high" for item in matched):
            counts["fat_high"] += 1
        if "外卖" in tags or "takeout" in categories or "fast_food" in categories:
            counts["takeout"] += 1
        if "油炸" in tags:
            counts["fried"] += 1
        if "蔬菜" in tags or "vegetable" in categories:
            counts["vegetable"] += 1
    return counts

def build_meal_suggestions(counts: dict, total: int) -> list:
    suggestions = []
    if total == 0:
        return ["先记录几餐，系统就能开始分析近期饮食趋势。"]
    if counts["vegetable"] < max(2, total // 4):
        suggestions.append("最近蔬菜出现频率偏低，下一餐可以优先补一份绿叶菜或菌菇。")
    if counts["protein_high"] < max(2, total // 4):
        suggestions.append("优质蛋白偏少，可以考虑鸡蛋、鱼虾、牛肉、鸡胸肉、豆腐或无糖酸奶。")
    if counts["salt_high"] >= max(2, total // 3):
        suggestions.append("高盐/重口记录偏多，后续可以少喝汤底、少加酱料，搭配清淡一餐。")
    if counts["sugar_high"] >= 2:
        suggestions.append("含糖饮料或甜食有点频繁，可以把奶茶/可乐换成无糖茶、咖啡或水。")
    if counts["fat_high"] >= max(2, total // 3) or counts["fried"] >= 2:
        suggestions.append("高脂或油炸偏多，下一餐尽量选蒸煮炖、轻食或少油炒菜。")
    if counts["takeout"] >= max(3, total // 2):
        suggestions.append("外卖占比偏高，可以在外卖里优先选有蔬菜、有蛋白、少汤汁的组合。")
    if counts["unknown"] >= max(2, total // 3):
        suggestions.append("有不少食物暂时没匹配到规则库，可以逐步把常吃项加入 food_rules.json。")
    return suggestions or ["这段时间饮食结构还算平衡，继续保持蛋白、蔬菜和主食的稳定搭配。"]

def build_meal_analysis(days: int = 7) -> dict:
    days = max(1, min(int(days or 7), 90))
    meals = [enriched_meal(m) for m in read_json_file(MEALS_FILE, [])]
    start_date = date.today() - timedelta(days=days - 1)
    recent = []
    for meal in meals:
        try:
            meal_date = datetime.strptime(meal.get("date", ""), "%Y-%m-%d").date()
        except Exception:
            continue
        if meal_date >= start_date:
            recent.append(meal)

    scores = [
        meal.get("analysis", {}).get("health_score")
        for meal in recent
        if meal.get("analysis", {}).get("health_score") is not None
    ]
    counts = meal_level_counts(recent)
    tag_counts = {}
    unknown_foods = []
    for meal in recent:
        for tag in meal.get("analysis", {}).get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        for food in meal.get("analysis", {}).get("unknown_foods", []):
            if food not in unknown_foods:
                unknown_foods.append(food)

    return {
        "days": days,
        "total_meals": len(recent),
        "average_health_score": round(sum(scores) / len(scores), 2) if scores else None,
        "counts": counts,
        "top_tags": sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:8],
        "unknown_foods": unknown_foods,
        "suggestions": build_meal_suggestions(counts, len(recent)),
    }

@app.get("/api/meals")
def get_meals():
    try:
        data = [enriched_meal(m) for m in read_json_file(MEALS_FILE, [])]
        return JSONResponse(data)
    except:
        return JSONResponse([])

@app.post("/api/meals")
async def add_meal(request: Request):
    try:
        payload = await request.json()
        meal_date = payload.get("date") or date.today().isoformat()
        meal_type = normalize_meal_type(payload.get("type"))
        content = str(payload.get("content") or "").strip()
        if not content:
            return JSONResponse({"status": "error", "message": "content is required"}, status_code=400)

        meal = {
            "date": meal_date,
            "type": meal_type,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "analysis": analyze_meal_content(content),
        }
        meals = read_json_file(MEALS_FILE, [])
        existing_index = next(
            (i for i, item in enumerate(meals)
             if item.get("date") == meal_date and normalize_meal_type(item.get("type")) == meal_type),
            None
        )
        if existing_index is None:
            meals.append(meal)
        else:
            meals[existing_index] = meal
        meals = sorted(meals, key=lambda item: (item.get("date", ""), item.get("timestamp", "")))[-120:]
        write_json_file(MEALS_FILE, meals)
        return JSONResponse({"status": "ok", "meal": meal, "analysis": build_meal_analysis(7)})
    except Exception as e:
        print(f"Error adding meal: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.put("/api/meals")
async def update_meal(request: Request):
    try:
        payload = await request.json()
        old_date = payload.get("old_date")
        old_type = normalize_meal_type(payload.get("old_type"))
        meal_date = payload.get("date") or old_date or date.today().isoformat()
        meal_type = normalize_meal_type(payload.get("type") or old_type)
        content = str(payload.get("content") or "").strip()
        if not old_date or not old_type:
            return JSONResponse({"status": "error", "message": "old_date and old_type are required"}, status_code=400)
        if not content:
            return JSONResponse({"status": "error", "message": "content is required"}, status_code=400)

        meals = read_json_file(MEALS_FILE, [])
        index = find_meal_index(meals, old_date, old_type)
        if index is None:
            return JSONResponse({"status": "error", "message": "Meal not found"}, status_code=404)

        meal = {
            "date": meal_date,
            "type": meal_type,
            "content": content,
            "timestamp": meals[index].get("timestamp") or datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "analysis": analyze_meal_content(content),
        }
        meals.pop(index)
        duplicate_index = find_meal_index(meals, meal_date, meal_type)
        if duplicate_index is None:
            meals.append(meal)
        else:
            meals[duplicate_index] = meal
        meals = sorted(meals, key=lambda item: (item.get("date", ""), item.get("timestamp", "")))[-120:]
        write_json_file(MEALS_FILE, meals)
        return JSONResponse({"status": "ok", "meal": meal, "analysis": build_meal_analysis(7)})
    except Exception as e:
        print(f"Error updating meal: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.delete("/api/meals")
async def delete_meal(request: Request):
    try:
        payload = await request.json()
        meal_date = payload.get("date")
        meal_type = normalize_meal_type(payload.get("type"))
        if not meal_date or not meal_type:
            return JSONResponse({"status": "error", "message": "date and type are required"}, status_code=400)

        meals = read_json_file(MEALS_FILE, [])
        index = find_meal_index(meals, meal_date, meal_type)
        if index is None:
            return JSONResponse({"status": "error", "message": "Meal not found"}, status_code=404)
        deleted = meals.pop(index)
        write_json_file(MEALS_FILE, meals)
        return JSONResponse({"status": "ok", "deleted": deleted, "analysis": build_meal_analysis(7)})
    except Exception as e:
        print(f"Error deleting meal: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/meals/analysis")
def get_meal_analysis(days: int = 7):
    try:
        return JSONResponse(build_meal_analysis(days))
    except Exception as e:
        print(f"Error building meal analysis: {e}")
        return JSONResponse({"days": days, "total_meals": 0, "suggestions": []})

FOOD_LEVELS = {"none", "low", "medium", "high"}

def normalize_food_rule(raw: dict) -> dict:
    rule = dict(raw or {})
    category = str(rule.get("category") or "other").strip().lower().replace(" ", "_")
    tags = rule.get("tags", [])
    if isinstance(tags, str):
        tags = [tag.strip() for tag in re.split(r"[,，、;；]", tags) if tag.strip()]
    if not isinstance(tags, list):
        tags = []

    try:
        health_score = int(rule.get("health_score", 0))
    except Exception:
        health_score = 0
    health_score = max(-3, min(3, health_score))

    normalized = {
        "category": category,
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:8],
        "health_score": health_score,
    }
    for field in ["protein", "carb", "fat", "fiber", "sugar", "salt"]:
        value = str(rule.get(field) or "none").strip().lower()
        normalized[field] = value if value in FOOD_LEVELS else "none"
    return normalized

def generate_food_rule(food_name: str, notes: str = "") -> dict:
    prompt = (
        "你是一个中文日常饮食轻量营养规则库维护助手。"
        "请为用户输入的食物生成一个粗略健康趋势分析规则，不要给医疗建议，不需要精确热量。"
        "只返回 JSON，不要 Markdown。\n\n"
        "JSON 格式必须是：\n"
        "{"
        "\"category\":\"staple|protein|vegetable|fruit|drink|snack|takeout|fast_food|dessert|restaurant|other\","
        "\"tags\":[\"中文标签\"],"
        "\"health_score\":-3到3的整数,"
        "\"protein\":\"none|low|medium|high\","
        "\"carb\":\"none|low|medium|high\","
        "\"fat\":\"none|low|medium|high\","
        "\"fiber\":\"none|low|medium|high\","
        "\"sugar\":\"none|low|medium|high\","
        "\"salt\":\"none|low|medium|high\","
        "\"aliases\":[\"常见别名，可为空\"]"
        "}\n\n"
        f"食物名称：{food_name}\n"
        f"补充说明：{notes or '无'}"
    )
    resp = agent.client.chat.completions.create(
        model=agent.MODEL,
        messages=[{"role": "user", "content": prompt}],
        timeout=30,
    )
    content = resp.choices[0].message.content.strip()
    match = re.search(r"\{[\s\S]*\}", content)
    data = json.loads(match.group(0) if match else content)
    return data

@app.get("/api/food-rules")
def get_food_rules():
    rules = load_food_rules()
    foods = rules.get("foods", {})
    return JSONResponse({
        "total": len(foods),
        "foods": sorted(foods.keys()),
    })

@app.post("/api/food-rules")
async def add_food_rule(request: Request):
    try:
        payload = await request.json()
        food_name = str(payload.get("name") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        if not food_name:
            return JSONResponse({"status": "error", "message": "name is required"}, status_code=400)

        generated = generate_food_rule(food_name, notes)
        rule = normalize_food_rule(generated)

        rules = load_food_rules()
        rules.setdefault("foods", {})
        rules.setdefault("aliases", {})
        rules["foods"][food_name] = rule

        aliases = generated.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [item.strip() for item in re.split(r"[,，、;；]", aliases) if item.strip()]
        if isinstance(aliases, list):
            for alias in aliases:
                alias = str(alias).strip()
                if alias and alias != food_name:
                    rules["aliases"][alias] = food_name

        write_json_file(FOOD_RULES_FILE, rules)
        return JSONResponse({
            "status": "ok",
            "name": food_name,
            "rule": rule,
            "aliases": [alias for alias, target in rules.get("aliases", {}).items() if target == food_name],
            "total": len(rules.get("foods", {})),
        })
    except Exception as e:
        print(f"Error adding food rule: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...), title: str = Form(None)):
    safe_filename = Path(file.filename or "").name
    if not safe_filename.lower().endswith('.pdf'):
        return JSONResponse({"error": "Only PDF files are supported."}, status_code=400)
    
    save_path = WORKDIR / safe_filename
    content = await file.read()
    save_path.write_bytes(content)
    
    # 在独立线程中运行（与 WebSocket 聊天的处理方式一致），
    # 避免同步 httpx 客户端在 async 事件循环中阻塞，导致代理设置失效。
    import asyncio
    res = await asyncio.to_thread(agent.run_ingest_paper, str(save_path), title)
    return JSONResponse({"result": res})

def paper_payload(paper_id, document="", metadata=None, distance=None):
    metadata = metadata or {}
    queued_ids = {item.get("paper_id") for item in load_reading_queue()}
    summary = metadata.get("summary") or document or ""
    raw_direction_tags = metadata.get("direction_tags", "")
    if isinstance(raw_direction_tags, list):
        direction_tags = [str(tag).strip() for tag in raw_direction_tags if str(tag).strip()]
    else:
        direction_tags = [
            tag.strip()
            for tag in str(raw_direction_tags or "").split(",")
            if tag.strip()
        ]
    payload = {
        "id": paper_id,
        "title": paper_id,
        "source": metadata.get("source", paper_id),
        "summary": summary,
        "arch_image": metadata.get("arch_image"),
        "direction_tags": direction_tags,
        "comment": metadata.get("comment", ""),
        "comment_updated_at": metadata.get("comment_updated_at", ""),
        "read_at": metadata.get("read_at", ""),
        "in_reading_queue": paper_id in queued_ids,
    }
    if distance is not None:
        payload["distance"] = distance
    return payload

def get_paper_by_id(paper_id: str):
    results = agent.paper_collection.get(ids=[paper_id])
    if not results or not results.get("ids"):
        return None
    metadata = results.get("metadatas", [{}])[0] or {}
    document = results.get("documents", [""])[0] or ""
    return paper_payload(results["ids"][0], document, metadata)

def load_reading_queue():
    if not READING_QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(READING_QUEUE_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_reading_queue(data):
    READING_QUEUE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def remove_from_reading_queue(paper_id: str):
    queue = load_reading_queue()
    updated = [item for item in queue if item.get("paper_id") != paper_id]
    if len(updated) != len(queue):
        save_reading_queue(updated)

def update_paper_metadata(paper_id: str, updates: dict):
    results = agent.paper_collection.get(ids=[paper_id])
    if not results or not results.get("ids"):
        return None
    document = results.get("documents", [""])[0] or ""
    metadata = results.get("metadatas", [{}])[0] or {}
    summary = metadata.get("summary") or document
    metadata.update({k: v for k, v in updates.items() if v is not None})
    search_document = build_paper_search_document(paper_id, summary, metadata)
    agent.paper_collection.upsert(
        ids=[paper_id],
        documents=[search_document],
        metadatas=[metadata],
    )
    return paper_payload(paper_id, search_document, metadata)

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

def weekly_read_count():
    today = agent.datetime.date.today()
    week_start = today - agent.datetime.timedelta(days=today.weekday())
    count = 0
    try:
        results = agent.paper_collection.get()
        for metadata in results.get("metadatas", []) or []:
            read_at = (metadata or {}).get("read_at", "")
            if read_at and read_at[:10] >= week_start.isoformat():
                count += 1
    except Exception as e:
        print(f"Error counting weekly reads: {e}")
    return count

def search_tokens(text: str) -> list:
    return [t for t in re.split(r"[\s,.;:!?，。！？；：、()\\[\\]{}<>\"']+", text.lower()) if t]

SEARCH_SCOPES = {"all", "title", "summary", "comment", "source", "direction"}

def scoped_search_fields(paper: dict, scope: str) -> dict:
    fields = {
        "title": (paper.get("title") or "").lower(),
        "source": (paper.get("source") or "").lower(),
        "summary": (paper.get("summary") or "").lower(),
        "comment": (paper.get("comment") or "").lower(),
        "direction": " ".join(paper.get("direction_tags") or []).lower(),
    }
    if scope == "all":
        return fields
    return {scope: fields.get(scope, "")}

def lexical_score(query: str, paper: dict, scope: str = "all") -> float:
    query_lower = query.lower().strip()
    tokens = search_tokens(query)
    fields = scoped_search_fields(paper, scope)
    title = fields.get("title", "")
    source = fields.get("source", "")
    summary = fields.get("summary", "")
    comment = fields.get("comment", "")
    direction = fields.get("direction", "")
    haystack = "\n".join(fields.values())

    score = 0.0
    if query_lower and title and query_lower in title:
        score += 5.0
    if query_lower and comment and query_lower in comment:
        score += 3.0
    if query_lower and summary and query_lower in summary:
        score += 2.0
    if query_lower and source and query_lower in source:
        score += 1.5
    if query_lower and direction and query_lower in direction:
        score += 3.0

    for token in tokens:
        if len(token) < 2:
            continue
        if token in title:
            score += 2.0
        if token in comment:
            score += 1.5
        if token in summary:
            score += 1.0
        if token in source:
            score += 0.5
        if token in direction:
            score += 1.5
        score += min(haystack.count(token), 3) * 0.15
    return score

def hybrid_score(query: str, paper: dict, scope: str = "all") -> float:
    distance = paper.get("distance")
    semantic = 0.0
    if scope in {"all", "summary"} and distance is not None:
        # Chroma distances are still useful for ordering, but a loose cutoff
        # makes almost every paper look relevant in a small library.
        semantic = max(0.0, (1.75 - float(distance)) * 1.5)
    return semantic + lexical_score(query, paper, scope)

def filter_search_candidates(papers: list, limit: int = 12) -> list:
    if not papers:
        return []

    papers.sort(key=lambda p: p["hybrid_score"], reverse=True)
    best_score = papers[0]["hybrid_score"]
    if best_score < 0.2:
        return []

    cutoff = max(0.35, best_score * 0.45)
    filtered = [
        paper for paper in papers
        if paper["hybrid_score"] >= cutoff or paper.get("lexical_score", 0) >= 1.0
    ]
    return filtered[:limit]

def rerank_papers(query: str, papers: list, limit: int = 12) -> list:
    if len(papers) <= 1:
        return papers

    candidates = papers[:limit]
    prompt_items = []
    for i, paper in enumerate(candidates):
        prompt_items.append({
            "rank": i,
            "title": paper.get("title"),
            "source": paper.get("source"),
            "summary": (paper.get("summary") or "")[:700],
            "comment": (paper.get("comment") or "")[:300],
            "hybrid_score": round(paper.get("hybrid_score", 0), 4),
        })

    prompt = (
        "You are reranking research papers for a personal paper knowledge base. "
        "Given the user's query and candidate papers, return ONLY a JSON array of candidate rank integers "
        "from most relevant to least relevant. Prefer exact title/method/comment matches, then semantic relevance.\n\n"
        f"Query: {query}\nCandidates:\n{json.dumps(prompt_items, ensure_ascii=False)}"
    )

    try:
        resp = agent.client.chat.completions.create(
            model=agent.MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=20,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", content)
        order = json.loads(match.group(0) if match else content)
        ordered = []
        seen = set()
        for idx in order:
            if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
                ordered.append(candidates[idx])
                seen.add(idx)
        ordered.extend(p for i, p in enumerate(candidates) if i not in seen)
        ordered.extend(papers[limit:])
        return ordered
    except Exception as e:
        print(f"Rerank failed, using hybrid order: {e}")
        return papers

@app.get("/api/papers")
def get_papers():
    try:
        results = agent.paper_collection.get()
        papers = []
        if results and results.get("ids"):
            for i in range(len(results["ids"])):
                title = results["ids"][i]
                metadata = {}
                summary = ""
                
                if results.get("metadatas") and i < len(results["metadatas"]) and results["metadatas"][i]:
                    metadata = results["metadatas"][i]
                if results.get("documents") and i < len(results["documents"]) and results["documents"][i]:
                    summary = results["documents"][i]
                
                papers.append(paper_payload(title, summary, metadata))
        return JSONResponse(papers)
    except Exception as e:
        print(f"Error getting papers: {e}")
        return JSONResponse([])

@app.get("/api/search")
def search_papers(q: str, scope: str = "all"):
    if not q:
        return get_papers()
    scope = scope if scope in SEARCH_SCOPES else "all"
    try:
        total_docs = agent.paper_collection.count()
        if total_docs <= 0:
            return JSONResponse([])

        results = agent.paper_collection.query(
            query_texts=[q],
            n_results=total_docs
        )
        papers = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            for i in range(len(results["ids"][0])):
                distance = results.get("distances")[0][i] if results.get("distances") and len(results["distances"]) > 0 else 0
                print(f"[\033[93mSearch Debug\033[0m] Match: {results['ids'][0][i][:20]}... Distance: {distance}")

                title = results["ids"][0][i]
                summary = ""
                metadata = {}
                
                if results.get("metadatas") and len(results["metadatas"]) > 0 and len(results["metadatas"][0]) > i:
                    metadata = results["metadatas"][0][i] or {}
                if results.get("documents") and len(results["documents"]) > 0 and len(results["documents"][0]) > i:
                    summary = results["documents"][0][i]

                paper = paper_payload(title, summary, metadata, distance)
                paper["lexical_score"] = lexical_score(q, paper, scope)
                paper["hybrid_score"] = hybrid_score(q, paper, scope)
                papers.append(paper)

        # Keep only plausible matches, then rerank that short list with the LLM.
        papers = filter_search_candidates(papers)
        papers = rerank_papers(q, papers)
        return JSONResponse(papers)
    except Exception as e:
        print(f"Error searching papers: {e}")
        return JSONResponse([])

@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str):
    try:
        agent.paper_collection.delete(ids=[paper_id])
        remove_from_reading_queue(paper_id)
        return JSONResponse({"status": "ok", "message": f"Deleted {paper_id}"})
    except Exception as e:
        print(f"Error deleting paper {paper_id}: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/papers/comment")
async def update_paper_comment(request: Request):
    try:
        payload = await request.json()
        paper_id = payload.get("paper_id")
        comment = (payload.get("comment") or "").strip()
        mark_read = bool(payload.get("mark_read"))
        if not paper_id:
            return JSONResponse({"status": "error", "message": "paper_id is required"}, status_code=400)

        now = agent.datetime.datetime.now().isoformat(timespec="seconds")
        updates = {"comment": comment, "comment_updated_at": now}
        if mark_read:
            updates["read_at"] = now

        paper = update_paper_metadata(paper_id, updates)
        if not paper:
            return JSONResponse({"status": "error", "message": "Paper not found"}, status_code=404)
        if mark_read:
            remove_from_reading_queue(paper_id)
        return JSONResponse({"status": "ok", "paper": paper})
    except Exception as e:
        print(f"Error updating paper comment: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/papers/tags")
async def update_paper_tags(request: Request):
    try:
        payload = await request.json()
        paper_id = payload.get("paper_id")
        auto_generate = bool(payload.get("auto_generate"))
        if not paper_id:
            return JSONResponse({"status": "error", "message": "paper_id is required"}, status_code=400)

        if auto_generate:
            existing = get_paper_by_id(paper_id)
            if not existing:
                return JSONResponse({"status": "error", "message": "Paper not found"}, status_code=404)
            tags = generate_paper_direction_tags(
                existing.get("title", paper_id),
                existing.get("summary", ""),
                existing.get("source", ""),
            )
        else:
            tags = normalize_direction_tags(payload.get("direction_tags", []))

        now = agent.datetime.datetime.now().isoformat(timespec="seconds")
        paper = update_paper_metadata(paper_id, {
            "direction_tags": ",".join(tags),
            "direction_tags_updated_at": now,
        })
        if not paper:
            return JSONResponse({"status": "error", "message": "Paper not found"}, status_code=404)
        return JSONResponse({"status": "ok", "paper": paper})
    except Exception as e:
        print(f"Error updating paper tags: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/reading-queue")
def get_reading_queue():
    queue = load_reading_queue()
    items = []
    for item in queue:
        paper = get_paper_by_id(item.get("paper_id", ""))
        if paper:
            paper["added_at"] = item.get("added_at", "")
            items.append(paper)
    return JSONResponse({
        "read_count_this_week": weekly_read_count(),
        "items": items,
    })

@app.post("/api/reading-queue")
async def add_reading_queue(request: Request):
    try:
        payload = await request.json()
        paper_id = payload.get("paper_id")
        if not paper_id:
            return JSONResponse({"status": "error", "message": "paper_id is required"}, status_code=400)
        if not get_paper_by_id(paper_id):
            return JSONResponse({"status": "error", "message": "Paper not found"}, status_code=404)

        queue = load_reading_queue()
        if not any(item.get("paper_id") == paper_id for item in queue):
            queue.append({
                "paper_id": paper_id,
                "added_at": agent.datetime.datetime.now().isoformat(timespec="seconds"),
            })
            save_reading_queue(queue)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        print(f"Error adding reading queue item: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.delete("/api/reading-queue/{paper_id:path}")
def delete_reading_queue_item(paper_id: str):
    remove_from_reading_queue(paper_id)
    return JSONResponse({"status": "ok"})

@app.get("/api/config")
def get_config():
    try:
        config = {"last_run": "", "keywords": ["AI-Generated Image Detection", "Agentic Workflow"]}
        if agent.STATUS_FILE.exists():
            try:
                config = json.loads(agent.STATUS_FILE.read_text())
            except:
                pass
        return JSONResponse(config)
    except Exception as e:
        print(f"Error reading config: {e}")
        return JSONResponse({"last_run": "", "keywords": ["AI-Generated Image Detection", "Agentic Workflow"]})

@app.post("/api/config")
async def update_config(request: Request):
    try:
        payload = await request.json()
        config = {"last_run": "", "keywords": ["AI-Generated Image Detection", "Agentic Workflow"]}
        if agent.STATUS_FILE.exists():
            try:
                config = json.loads(agent.STATUS_FILE.read_text())
            except:
                pass
        
        if "keywords" in payload:
            config["keywords"] = payload["keywords"]
        if "last_run" in payload:
            config["last_run"] = payload["last_run"]
            
        agent.STATUS_FILE.write_text(json.dumps(config, indent=2))
        return JSONResponse({"status": "success", "config": config})
    except Exception as e:
        print(f"Error updating config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/tasks")
def get_tasks():
    return JSONResponse(agent.get_pipeline_runs())

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
