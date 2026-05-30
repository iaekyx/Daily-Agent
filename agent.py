#!/usr/bin/env python3
# Harness: integration -- tools aren't just in your code.
"""
s19_mcp_plugin.py - MCP & Plugin System (Qwen / DashScope Version)
"""
import re
import json
import subprocess
import threading
import asyncio
from scheduler import AgentScheduler
import datetime
import concurrent.futures
import uuid
import time

from agent_runtime.config import MODEL, WORKDIR, client
from agent_runtime.fs import safe_path
from agent_runtime.mcp import MCPClient, mcp_router, plugin_loader
from agent_runtime.permissions import permission_gate
from agent_runtime.tasks import get_pipeline_runs, register_pipeline, update_pipeline

from agent_runtime.memory import paper_collection, run_ingest_paper, run_search_memory

# -- Native tool implementations --
def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous): return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired: return "Error: Timeout (120s)"

def run_read(path: str) -> str:
    try: return safe_path(path).read_text()[:50000]
    except Exception as e: return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e: return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content: return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e: return f"Error: {e}"

def run_get_current_datetime() -> str:
    now = datetime.datetime.now()
    payload = {
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][now.weekday()],
        "weekday_zh": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()],
        "timezone": "local",
    }
    return json.dumps(payload, ensure_ascii=False)

# 🚨 新增：包装提交流水线的 Native 工具
def run_submit_pipeline(tasks: list) -> str:
    if not tasks:
        return "Error: 没有提供任何任务。"
    
    pipeline_id = f"pipeline-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    register_pipeline(pipeline_id, tasks)

    def on_update(snapshot):
        update_pipeline(pipeline_id, snapshot)

    scheduler = AgentScheduler(work_dir=WORKDIR, on_update=on_update)
    for t in tasks:
        scheduler.add_task(t["task_id"], t["command"], t.get("depends_on", []))
        
    def background_worker(sched):
        # 创建一个新的事件循环供后台线程使用
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(sched.start_pipeline())
        loop.close()

    # daemon=True 意味着如果你用 Ctrl+C 退出了主程序，后台训练也会跟着退出
    thread = threading.Thread(target=background_worker, args=(scheduler,), daemon=True)
    thread.start()
    
    return f"成功！已将 {len(tasks)} 个级联任务提交至后台异步调度器。Pipeline ID: {pipeline_id}。你可以通过 /api/tasks 查看执行状态。"

def run_task_agent(role_prompt: str, task_prompt: str, tools: list | None) -> str:
    """专为后台 Worker Agent 设计的闭环引擎，自动处理多轮工具调用直到任务完成"""
    messages = [
        {"role": "system", "content": role_prompt},
        {"role": "user", "content": task_prompt}
    ]
    
    while True:
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools, 
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or "（Worker 未返回有效文本）"

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_input = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                tool_input = {}

            # 后台 Worker 自动执行，使用 auto 权限 (由上一层控制)
            decision = permission_gate.check(tool_name, tool_input)
            try:
                if decision["behavior"] == "deny":
                    output = f"Permission denied: {decision['reason']}"
                else:
                    # 对于高风险强制询问，如果是后台，这里默认按 auto 逻辑跳过询问直接允许(为防死锁)
                    output = handle_tool_call(tool_name, tool_input)
            except Exception as e:
                output = f"Error: {e}"
                
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": normalize_tool_result(tool_name, str(output), decision.get("intent")),
            })

def run_delegate_tasks(tasks: list) -> str:
    """主 Agent 调用的多智能体分发工具"""
    if not tasks:
        return "Error: 没有提供任何任务。"

    print(f"\n[\033[94mManager\033[0m] 👑 主智能体决定摇人！正在召唤 {len(tasks)} 个下属 Agent 并行工作...")
    all_tools = build_tool_pool()
    
    # 🚨 防止“盗梦空间”无限套娃：下属 Agent 不能再召唤下属！
    worker_tools = [t for t in all_tools if t["function"]["name"] != "delegate_tasks"]

    # 🚨 防止后台多线程同时弹出权限询问导致终端乱码，临时开启 auto 模式
    original_mode = permission_gate.mode
    permission_gate.mode = "auto"

    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            future_to_task = {}
            for i, t in enumerate(tasks):
                role = t.get("role", "你是一个高效的 AI 助手。")
                task_desc = t.get("task_description", "")
                print(f"   -> 派发任务给 Worker {i+1} : {task_desc[:30]}...")
                future = executor.submit(run_task_agent, role, task_desc, worker_tools)
                future_to_task[future] = f"Worker {i+1}"

            for future in concurrent.futures.as_completed(future_to_task):
                worker_name = future_to_task[future]
                try:
                    res = future.result()
                    results.append(f"[{worker_name} 的汇报]:\n{res}\n")
                    print(f"   ✅ {worker_name} 汇报完毕！")
                except Exception as e:
                    results.append(f"[{worker_name} 执行失败]: {e}\n")
                    print(f"   ❌ {worker_name} 发生崩溃！")
    finally:
        permission_gate.mode = original_mode
    print("[\033[94mManager\033[0m] 所有下属工作结束，主智能体继续接管...\n")

    return "以下是所有下属 Agent 的并发执行报告，请根据这些信息汇总并回答用户：\n\n" + "\n---\n".join(results)

MEAL_DB = WORKDIR / "meals.json"

def run_meal_manager(action: str, meal_data: dict = None) -> str:
    """餐饮管理核心逻辑：支持记录与查询饮食历史。"""
    # --- 1. 处理餐饮记录 (meals.json) ---
    if action in ["log", "get_history", "delete"]:
        if not MEAL_DB.exists(): MEAL_DB.write_text("[]")
        try: meals = json.loads(MEAL_DB.read_text())
        except: meals = []

        if action in ["log", "delete"]:
            target_date = meal_data.get("date", "")
            # 自动清洗：如果没传，或者是"今天"、"昨天"，或者是其他不符合 YYYY-MM-DD 的乱七八糟格式
            if not target_date or target_date == "今天":
                target_date = datetime.date.today().isoformat()
            elif target_date == "昨天":
                target_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            elif target_date == "前天":
                target_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
            elif not re.match(r"^\d{4}-\d{2}-\d{2}$", target_date):
                # 如果是其他奇奇怪怪的非标准格式，强制按今天处理（或者你可以让它报错返回）
                print(f"⚠️ [警告] 纠正了非标准的日期格式: {target_date} -> {datetime.date.today().isoformat()}")
                target_date = datetime.date.today().isoformat()

            target_type = meal_data.get("type")
            
            existing_index = next((i for i, m in enumerate(meals) if m.get("date") == target_date and m.get("type") == target_type), None)

            if action == "delete":
                if existing_index is not None:
                    deleted = meals.pop(existing_index)
                    MEAL_DB.write_text(json.dumps(meals, indent=2, ensure_ascii=False))
                    return f"🗑️ 已删除 {target_date} {target_type} 的记录（原内容：{deleted['content']}）。"
                return f"⚠️ 找不到 {target_date} {target_type} 的记录。"

            elif action == "log":
                target_content = meal_data.get("content")
                if existing_index is not None:
                    meals[existing_index]["content"] = target_content
                    action_msg = f"🔄 已将 {target_date} {target_type} 修改为: '{target_content}'。"
                else:
                    meals.append({"date": target_date, "type": target_type, "content": target_content})
                    action_msg = f"✅ 已新增 {target_date} {target_type}: {target_content}"
                MEAL_DB.write_text(json.dumps(meals[-30:], indent=2, ensure_ascii=False))
                return action_msg

        elif action == "get_history":
            three_days_ago = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
            history = [m for m in meals if m['date'] >= three_days_ago]
            if not history: return "目前没有近三天的餐饮记录。"
            return "📅 近三天餐饮历史：\n" + "\n".join([f"- {m['date']} {m['type']}: {m['content']}" for m in history])

    return "Error: 未知的 action 指令。"
    
NATIVE_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw.get("command", "")),
    "read_file":        lambda **kw: run_read(kw.get("path", "")),
    "write_file":       lambda **kw: run_write(kw.get("path", ""), kw.get("content", "")),
    "edit_file":        lambda **kw: run_edit(kw.get("path", ""), kw.get("old_text", ""), kw.get("new_text", "")),
    "get_current_datetime": lambda **kw: run_get_current_datetime(),
    "submit_pipeline":  lambda **kw: run_submit_pipeline(kw.get("tasks", [])), # 🚨 挂载新功能
    "delegate_tasks":   lambda **kw: run_delegate_tasks(kw.get("tasks", [])), # 🚨 新增
    "meal_manager":     lambda **kw: run_meal_manager(kw.get("action", ""), kw.get("meal_data")),
    # 🚨 新增：记忆工具入口
    "ingest_paper":     lambda **kw: run_ingest_paper(kw.get("pdf_path", ""), kw.get("custom_title")),
    "search_memory":    lambda **kw: run_search_memory(kw.get("query", "")),
}

NATIVE_TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace exact text in file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "get_current_datetime", "description": "返回本机当前日期、时间和星期。凡是涉及今天、昨日、本周、每日早报、最新论文等日期敏感任务时，必须先调用此工具，不要凭模型记忆猜日期。", "parameters": {"type": "object", "properties": {}}}},
    
    # 🚨 注册新的 Pipeline 工具给大模型
    {"type": "function", "function": {
        "name": "submit_pipeline", 
        "description": "向后台任务调度器提交一个有前后依赖关系的流水线（例如：训练完再测试）。此任务会在后台静默运行，不会阻塞当前的对话。", 
        "parameters": {
            "type": "object", 
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "任务列表。每个任务必须包含 task_id 和 command。如果需要等待前置任务完成，配置 depends_on 列表。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string", "description": "唯一任务名称，如 'train_v1'"},
                            "command": {"type": "string", "description": "要在终端执行的完整命令，如 'python train.py'"},
                            "depends_on": {
                                "type": "array", 
                                "items": {"type": "string"},
                                "description": "依赖的前置 task_id 列表。只有这些任务成功了，本任务才会执行。"
                            }
                        },
                        "required": ["task_id", "command"]
                    }
                }
            }, 
            "required": ["tasks"]
        }
    }},
    {"type": "function", "function": {
        "name": "delegate_tasks", 
        "description": "【高级能力】当你认为用户的任务可以拆分成多个独立的子任务并【并行】执行时（例如同时查几篇不同的论文，或同时检查更新和检索新文章），使用此工具召唤多个 Worker Agent 同时为你打工。这会大幅缩短总耗时。", 
        "parameters": {
            "type": "object", 
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "需要分配的独立任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "给这个下属设定的身份，例如'你是一个文献检索专家'"},
                            "task_description": {"type": "string", "description": "下属需要执行的具体指令，请详细说明它能用什么工具，要找什么"}
                        },
                        "required": ["role", "task_description"]
                    }
                }
            }, 
            "required": ["tasks"]
        }
    }},
    {"type": "function", "function": {
        "name": "meal_manager",
        "description": "【生活助手】管理餐饮历史。用于记录/查询用户每天吃了什么。当你被要求分析饮食或推荐后续吃什么时，你必须先执行 'get_history' 查看近期吃了什么，再基于历史给出健康建议。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string", 
                    "enum": ["log", "get_history", "delete"],
                    "description": "log/delete/get_history 用于管理具体哪天吃了什么。"
                },
                "meal_data": {
                    "type": "object",
                    "description": "当 action 为 log/delete 时必须提供。包含日期(date)、餐次(lunch/dinner)、菜品(content)。",
                    "properties": {
                        # 🚨 爆改这里的 description
                        "date": {
                            "type": "string", 
                            "description": "【必须是 'YYYY-MM-DD' 格式的绝对日期】（例如 '2026-05-06'）。严禁填入'今天'、'昨天'等相对词！如果你收到相对时间词，请务必根据当前系统时间自行推算出正确的绝对日期。"
                        },
                        "type": {"type": "string", "enum": ["lunch", "dinner"]},
                        "content": {"type": "string"}
                    }
                }
            },
            "required": ["action"]
        }
    }},
    # 🚨 新增：入库工具
    {"type": "function", "function": {
        "name": "ingest_paper",
        "description": "【长期记忆】当用户提供了一篇新的 PDF 论文并要求阅读/总结/记住时调用。该工具会自动提取 PDF 内容，总结其创新点和模型架构，并永久存入你的向量数据库中。",
        "parameters": {
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string", "description": "本地 PDF 文件的相对或绝对路径，例如 'papers/attention_is_all_you_need.pdf'"}
            },
            "required": ["pdf_path"]
        }
    }},
    # 🚨 新增：检索工具
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "【长期记忆】当用户询问以前看过的论文、寻找特定算法灵感、或询问'我之前看过哪篇关于XXX的论文'时调用。通过语义模糊搜索，从你的历史记忆库中提取最相关的论文总结。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词或问题，例如 '跨模态特征融合的网络架构' 或 '关于 AIGC 图像检测的最新方法'"}
            },
            "required": ["query"]
        }
    }}
]

def build_tool_pool() -> list:
    all_tools = list(NATIVE_TOOLS)
    mcp_tools = mcp_router.get_all_tools()
    native_names = {t["function"]["name"] for t in all_tools}
    for tool in mcp_tools:
        if tool["function"]["name"] not in native_names:
            all_tools.append(tool)
    return all_tools

def handle_tool_call(tool_name: str, tool_input: dict) -> str:
    if mcp_router.is_mcp_tool(tool_name):
        return mcp_router.call(tool_name, tool_input)
    handler = NATIVE_HANDLERS.get(tool_name)
    if handler: return handler(**tool_input)
    return f"Unknown tool: {tool_name}"

def normalize_tool_result(tool_name: str, output: str, intent: dict | None = None) -> str: 
    intent = intent or permission_gate.normalize(tool_name, {})
    status = "error" if "Error:" in output or "MCP Error:" in output else "ok"
    payload = {
        "source": intent["source"], "server": intent.get("server"),
        "tool": intent["tool"], "risk": intent["risk"],
        "status": status, "preview": output[:10000], 
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

def _has_stream_callbacks() -> bool:
    return all(
        name in globals() and globals().get(name)
        for name in ("web_stream_start_callback", "web_stream_delta_callback", "web_stream_end_callback")
    )

def _build_non_stream_message(messages: list, tools: list) -> dict:
    response = client.chat.completions.create(
        model=MODEL, messages=messages, tools=tools, timeout=60,
    )
    return response.choices[0].message.model_dump(exclude_none=True)

def _using_gemini() -> bool:
    return "gemini" in MODEL.lower() or "generativelanguage.googleapis.com" in str(getattr(client, "base_url", ""))

def _chunk_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _build_stream_message(messages: list, tools: list) -> dict:
    content_parts = []

    web_stream_start_callback()

    stream = client.chat.completions.create(
        model=MODEL, messages=messages, tools=tools, tool_choice="none", stream=True, timeout=60,
    )

    for chunk in stream:
        choices = _chunk_value(chunk, "choices") or []
        if not choices:
            continue
        delta = _chunk_value(choices[0], "delta") or {}

        piece = _chunk_value(delta, "content")
        if piece:
            content_parts.append(piece)
            web_stream_delta_callback(piece)

    web_stream_end_callback()

    content = "".join(content_parts)
    if not content:
        raise RuntimeError("Streaming completed without content or tool calls")

    return {"role": "assistant", "content": content}

def _stream_text_to_web(content: str):
    if not content:
        return
    web_stream_start_callback()

    total = len(content)
    if total <= 120:
        max_chunk, delay = 3, 0.045
    elif total <= 600:
        max_chunk, delay = 8, 0.03
    elif total <= 1500:
        max_chunk, delay = 18, 0.018
    else:
        max_chunk, delay = 36, 0.008

    buffer = ""
    for ch in content:
        buffer += ch
        should_flush = (
            len(buffer) >= max_chunk
            or ch in "\n，。！？；,.!?;: "
            or (total <= 300 and ord(ch) < 128)
        )
        if should_flush:
            web_stream_delta_callback(buffer)
            buffer = ""
            time.sleep(delay)

    if buffer:
        web_stream_delta_callback(buffer)
    web_stream_end_callback()

def build_agent_message(messages: list, tools: list) -> dict:
    if not _has_stream_callbacks():
        return _build_non_stream_message(messages, tools)

    if _using_gemini():
        message = _build_non_stream_message(messages, tools)
        if not message.get("tool_calls"):
            _stream_text_to_web(message.get("content", ""))
        return message

    try:
        return _build_stream_message(messages, tools)
    except Exception as stream_error:
        if "web_stream_end_callback" in globals() and web_stream_end_callback:
            web_stream_end_callback()
        print(f"[\033[33mStreaming fallback\033[0m] {stream_error}")
        message = _build_non_stream_message(messages, tools)
        if not message.get("tool_calls"):
            _stream_text_to_web(message.get("content", ""))
        return message

def agent_loop(messages: list):
    tools = build_tool_pool()

    while True:
        # 动态加载用户配置的关键词
        config = {"keywords": ["AI-Generated Image Detection", "Agentic Workflow"]}
        if STATUS_FILE.exists():
            try:
                config = json.loads(STATUS_FILE.read_text())
            except: pass
        active_keywords = config.get("keywords", [])
        
        system_prompt = (
            f"You are a highly capable Manager Agent at {WORKDIR}.\n"
            "You have both native tools and MCP tools available.\n"
            "MCP tools are prefixed with mcp__{server}__{tool}.\n"
            "【日期原则】：凡是用户提到今天、昨天、本周、每日早报、今日最新、最近等日期敏感任务，你必须先调用 `get_current_datetime` 获取当前本机日期，再基于工具结果回答或检索；不要凭模型记忆猜日期。\n"
            f"【重点关注领域】：用户当前高度关注的学术关键词是：{', '.join(active_keywords)}。在用户未明确指定检索词时，你必须严格使用这些关键词作为默认搜索词调用 arXiv 检索，绝对不允许使用 'Large Language Models' 或其他无关的默认词。\n"
            "【代码仓库绑定原则】：在为论文寻找并绑定 GitHub 官方仓库时，必须执行严谨的双重校验：（1）仓库 README/描述中是否明确提及该论文标题或作者；（2）仓库所有者/贡献者是否属于论文作者。严禁仅凭项目名称相同就盲目绑定（例如重名的毕业设计、无关的开源工具或商业项目）。\n"
            "【重要原则】：对于简单任务，你自己调用工具解决。对于复杂且可并行的任务（如对比两个不同的领域，或者需要同时调用多个极其耗时的工具），你【必须】调用 `delegate_tasks` 工具派发给下属执行，最后你来做汇总总结。"
        )
        if not messages or messages[0].get("role") != "system":
             messages.insert(0, {"role": "system", "content": system_prompt})
        else:
             messages[0]["content"] = system_prompt

        try:
            message = build_agent_message(messages, tools)
        except Exception as e:
            err_msg = str(e)
            provider_name = "Google Gemini" if "gemini" in MODEL.lower() else "阿里 DashScope"
            if "Connection error" in err_msg or "EOF occurred" in err_msg:
                fallback_msg = f"⚠️ **网络连接异常**：由于您本地代理软件或网络环境的 TLS 握手失败，Agent 无法连接到 {provider_name} 接口。\n\n**原始报错信息：**\n{err_msg}"
            else:
                fallback_msg = f"API Request Failed: {err_msg}"
                
            if 'web_finish_callback' in globals() and web_finish_callback:
                web_finish_callback(fallback_msg)
            else:
                print(f"Error: {fallback_msg}")
            return

        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            if 'web_finish_callback' in globals() and web_finish_callback:
                if not _has_stream_callbacks():
                    web_finish_callback(message.get("content", ""))
            return

        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            tool_name = function.get("name", "")
            try:
                tool_input = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {}

            decision = permission_gate.check(tool_name, tool_input)
            try:
                if decision["behavior"] == "deny":
                    output = f"Permission denied: {decision['reason']}"
                elif decision["behavior"] == "ask" and not permission_gate.ask_user(decision["intent"], tool_input):
                    output = f"Permission denied by user: {decision['reason']}"
                else:
                    output = handle_tool_call(tool_name, tool_input)
            except Exception as e:
                output = f"Error: {e}"
                
            color = "\033[35m" if "mcp__" in tool_name else "\033[36m"
            log_str = f"{color}> {tool_name}:\033[0m {str(output)[:300]}"
            print(log_str)
            if 'web_print_callback' in globals() and web_print_callback:
                web_print_callback(log_str)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": normalize_tool_result(tool_name, str(output), decision.get("intent")),
            })

STATUS_FILE = WORKDIR / "agent_status.json"

def get_daily_update(router, keywords):
    print("[\033[94mSystem\033[0m] 检测到今日首次启动，正在执行自动巡检...")
    today = datetime.date.today().isoformat()
    report_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    
    query = f"""
        今天是 {today}，请帮我生成一份【每日学术早报】。
        论文检索窗口固定为昨日：{report_date}。不要检索今天的论文，因为今天发布尚不完整。

        你需要完成以下 3 个**完全独立**的子任务：
        1. 检查收藏夹中【已有代码】仓库的最新动态 (check_repo_updates)。
        2. 为收藏夹中【无代码】的论文寻找并绑定官方仓库 (get_missing_repo_candidates)。
        3. 检索关于 '{" 和 ".join(keywords)}' 的昨日论文 (search_arxiv，严查发布日期必须等于 {report_date})。

        【强制执行策略】：
        这三个任务互相没有依赖且非常耗时。作为高阶 Manager，你**绝不能**自己按顺序挨个执行！
        你必须立刻调用 `delegate_tasks` 工具，将这 3 个任务包装成独立的 Task，派发给 3 个专属下属 Agent **并行执行**！

        当所有下属向你汇报完成后，请你整合他们的结果，用 Markdown 输出漂亮的早报：
        ### 🔄 收藏夹动态
        (列出仓库代码更新，以及今天新找到并绑定的仓库)
        ### 📄 arXiv 新论文
        (只列出发布日期为 {report_date} 的论文标题和链接，没有就写“昨日无新论文”)
        """
    
    auto_history = [{"role": "user", "content": query}]
    agent_loop(auto_history)
    
    if auto_history:
        final_msg = auto_history[-1].get("content", "")
        print(f"\n{final_msg}\n")

def check_and_run_daily_task(router):
    today = datetime.date.today().isoformat()
    config = {"last_run": "", "keywords": ["AI-Generated Image Detection", "Agentic Workflow"]} 
    
    if STATUS_FILE.exists():
        try:
            config = json.loads(STATUS_FILE.read_text())
        except: pass

    if config.get("last_run") != today:
        get_daily_update(router, config.get("keywords", []))
        config["last_run"] = today
        STATUS_FILE.write_text(json.dumps(config, indent=2))
    else:
        print("[\033[90mSystem\033[0m] 今日已完成自动检索，跳过。")

def init_agent():
    found = plugin_loader.scan() 
    if found:
        print(f"[\033[92mPlugins loaded: {', '.join(found)}\033[0m]")
        for server_name, config in plugin_loader.get_mcp_servers().items():
            mcp_client = MCPClient(server_name, config.get("command", ""), config.get("args", []))
            if mcp_client.connect():
                mcp_client.list_tools() 
                mcp_router.register_client(mcp_client)
                print(f"[\033[35mMCP\033[0m] Connected to {server_name}")
    
    tool_count = len(build_tool_pool())
    mcp_count = len(mcp_router.get_all_tools())
    print(f"[\033[94mTool pool initialized: {tool_count} tools ({mcp_count} from MCP)\033[0m]")

def cleanup_agent():
    print("[\033[90mSystem\033[0m] Disconnecting MCP servers...")
    for c in mcp_router.clients.values():
        c.disconnect()

if __name__ == "__main__":
    init_agent()

    try:
        check_and_run_daily_task(mcp_router)
    except Exception as e:
        print(f"[\033[31mError\033[0m] 自动任务失败: {e}")
    
    history = []
    while True:
        try:
            query = input("\033[36ms19 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        # 🚨 修复 1：把 "" 从退出条件里拿掉
        if query.strip().lower() in ("q", "exit"):
            break
            
        # 🚨 修复 2：如果仅仅是按了回车（空字符串），直接 continue 刷新提示符
        if not query.strip():
            continue

        if query.strip() == "/tools":
            for tool in build_tool_pool():
                name = tool["function"]["name"]
                desc = tool["function"].get("description", "")[:60]
                prefix = "[\033[35mMCP\033[0m]    " if name.startswith("mcp__") else "[\033[32mNative\033[0m] "
                print(f"  {prefix}{name}: {desc}")
            continue

        if query.strip() == "/mcp":
            if mcp_router.clients:
                for name, c in mcp_router.clients.items():
                    tools = c.get_agent_tools()
                    print(f"  \033[35m{name}\033[0m: {len(tools)} tools")
            else:
                print("  \033[90m(no MCP servers connected)\033[0m")
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)
        
        final_response = history[-1].get("content")
        if final_response:
            print(final_response)
        print()

    cleanup_agent()
