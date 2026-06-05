from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
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
# 获取或创建用户长期记忆合集：偏好、项目决策、长期事实、待办等
user_memory_collection = chroma_client.get_or_create_collection(name="user_memory")
# 架构图存储目录
ARCH_IMAGES_DIR = CHROMA_PATH / "arch_images"
ARCH_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
USER_PROFILE_PATH = WORKDIR / "data" / "user_profile.json"
CONVERSATIONS_PATH = WORKDIR / "data" / "conversations.json"
MAX_ACTIVE_MESSAGES = 16

DEFAULT_USER_PROFILE = {
    "version": "0.1",
    "summary": "",
    "long_term_goals": [],
    "research_interests": [],
    "project_preferences": [],
    "agent_policies": {},
    "active_projects": [],
    "diet_preferences": [],
    "todos": [],
    "updated_at": "",
}

def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] JSON 读取失败 {path.name}: {e}")
    return default

def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

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

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def _unique_list(values, limit: int = 20) -> list:
    result = []
    for value in values or []:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result

def normalize_user_profile(profile: dict | None) -> dict:
    raw = dict(DEFAULT_USER_PROFILE)
    if isinstance(profile, dict):
        raw.update(profile)

    normalized = dict(DEFAULT_USER_PROFILE)
    normalized["version"] = str(raw.get("version") or DEFAULT_USER_PROFILE["version"])
    normalized["summary"] = str(raw.get("summary") or "").strip()[:1200]
    normalized["long_term_goals"] = _unique_list(raw.get("long_term_goals"), 20)
    normalized["research_interests"] = _unique_list(raw.get("research_interests"), 20)
    normalized["project_preferences"] = _unique_list(raw.get("project_preferences"), 30)
    normalized["active_projects"] = _unique_list(raw.get("active_projects"), 20)
    normalized["diet_preferences"] = _unique_list(raw.get("diet_preferences"), 20)
    normalized["todos"] = _unique_list(raw.get("todos"), 30)
    agent_policies = raw.get("agent_policies")
    normalized["agent_policies"] = agent_policies if isinstance(agent_policies, dict) else {}
    normalized["updated_at"] = str(raw.get("updated_at") or "").strip()
    return normalized

def load_user_profile() -> dict:
    try:
        if USER_PROFILE_PATH.exists():
            return normalize_user_profile(json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 用户画像读取失败: {e}")
    return normalize_user_profile(DEFAULT_USER_PROFILE)

def save_user_profile(profile: dict) -> dict:
    normalized = normalize_user_profile(profile)
    normalized["updated_at"] = _now_iso()
    _write_json(USER_PROFILE_PATH, normalized)
    return normalized

def load_conversations_store() -> dict:
    store = _read_json(CONVERSATIONS_PATH, {"version": "0.1", "conversations": {}})
    if not isinstance(store, dict):
        store = {"version": "0.1", "conversations": {}}
    store.setdefault("version", "0.1")
    store.setdefault("conversations", {})
    if not isinstance(store["conversations"], dict):
        store["conversations"] = {}
    return store

def save_conversations_store(store: dict):
    _write_json(CONVERSATIONS_PATH, store)

def create_conversation(title: str = "") -> dict:
    store = load_conversations_store()
    conversation_id = f"conv-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    conversation = {
        "id": conversation_id,
        "title": title.strip()[:80] if title else "新对话",
        "summary": "",
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    store["conversations"][conversation_id] = conversation
    save_conversations_store(store)
    return conversation

def get_conversation(conversation_id: str | None = None) -> dict:
    store = load_conversations_store()
    if conversation_id and conversation_id in store["conversations"]:
        return store["conversations"][conversation_id]
    if store["conversations"]:
        return sorted(
            store["conversations"].values(),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )[0]
    return create_conversation()

def list_conversations() -> list[dict]:
    store = load_conversations_store()
    items = []
    for conv in store["conversations"].values():
        items.append({
            "id": conv.get("id"),
            "title": conv.get("title") or "新对话",
            "summary": conv.get("summary", ""),
            "message_count": len(conv.get("messages", [])),
            "created_at": conv.get("created_at", ""),
            "updated_at": conv.get("updated_at", ""),
        })
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

def delete_conversation(conversation_id: str) -> dict:
    store = load_conversations_store()
    deleted = bool(conversation_id and conversation_id in store["conversations"])
    if deleted:
        store["conversations"].pop(conversation_id, None)
        save_conversations_store(store)
    remaining = list_conversations()
    next_conversation = get_conversation(remaining[0]["id"]) if remaining else create_conversation()
    return {
        "deleted": deleted,
        "conversation": next_conversation,
        "conversations": list_conversations(),
    }

def save_conversation(conversation: dict) -> dict:
    store = load_conversations_store()
    conversation["updated_at"] = _now_iso()
    store["conversations"][conversation["id"]] = conversation
    save_conversations_store(store)
    return conversation

def conversation_messages_for_agent(conversation: dict) -> list[dict]:
    messages = []
    summary = str(conversation.get("summary") or "").strip()
    if summary:
        messages.append({
            "role": "system",
            "content": f"【当前对话历史摘要】\n{summary}\n请把它作为本对话之前内容的压缩上下文。",
        })
    messages.extend(conversation.get("messages", []))
    return messages

def _fallback_conversation_title(user_message: str) -> str:
    text = str(user_message or "").strip()
    if not text:
        return "新对话"
    cleaned = re.sub(r"\s+", " ", text)
    return cleaned[:28] + ("..." if len(cleaned) > 28 else "")

def _clean_conversation_title(title: str, user_message: str) -> str:
    cleaned = re.sub(r"[\r\n\"'`#*<>]", "", str(title or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^(标题|对话标题|主题)[:：]\s*", "", cleaned).strip()
    if not cleaned or cleaned == "新对话":
        return _fallback_conversation_title(user_message)
    return cleaned[:24] + ("..." if len(cleaned) > 24 else "")

def _generate_conversation_title(user_message: str, assistant_message: str = "") -> str:
    fallback = _fallback_conversation_title(user_message)
    prompt = f"""
请根据下面这轮对话，为当前会话生成一个简洁中文标题。

要求：
1. 标题应概括用户真正想做的事，而不是复述“帮我改一下”这类泛泛表述。
2. 6 到 12 个中文字符左右，最多不超过 18 个中文字符。
3. 不要使用引号、Markdown、句号、冒号，不要输出解释。
4. 如果信息不足，返回一个比“新对话”更具体的标题。

用户消息：
{str(user_message or "")[:1200]}

助手回复：
{str(assistant_message or "")[:1600]}
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=20,
        )
        content = resp.choices[0].message.content
        return _clean_conversation_title(content, user_message)
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 对话标题生成失败，使用兜底标题: {e}")
        return fallback

def summarize_conversation_messages(previous_summary: str, messages: list[dict]) -> str:
    if not messages:
        return previous_summary
    transcript = "\n".join(
        f"{m.get('role', '')}: {str(m.get('content', ''))[:1200]}"
        for m in messages
        if m.get("role") in ("user", "assistant")
    )
    prompt = f"""
你是「研习助手 Daily Agent」的对话压缩器。请把较早的对话内容压缩成一段可供后续继续对话使用的中文摘要。

要求：
1. 保留用户明确提出的问题、项目修改决策、未完成待办、关键结论。
2. 保留和本项目模块相关的信息：智能体控制台、学术动态面板、文献记忆库、本周待读、饮食生活、Memory、MCP、RAG、每日简报。
3. 不要保存 API key、密码、token 等敏感信息。
4. 摘要控制在 800 字以内，不要 Markdown 标题。

已有摘要：
{previous_summary or "无"}

需要压缩的新旧对话：
{transcript}
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        return resp.choices[0].message.content.strip()[:1600]
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 对话压缩失败，使用拼接兜底: {e}")
        fallback = (previous_summary + "\n" + transcript).strip()
        return fallback[-1600:]

def maybe_compress_conversation(conversation: dict, keep_last: int = MAX_ACTIVE_MESSAGES) -> tuple[dict, bool]:
    messages = conversation.get("messages", [])
    if len(messages) <= keep_last:
        return conversation, False

    split_at = max(0, len(messages) - keep_last)
    older_messages = messages[:split_at]
    recent_messages = messages[split_at:]
    conversation["summary"] = summarize_conversation_messages(conversation.get("summary", ""), older_messages)
    conversation["messages"] = recent_messages
    return conversation, True

def update_conversation_after_turn(conversation_id: str, user_message: str, agent_messages: list[dict]) -> tuple[dict, bool]:
    conversation = get_conversation(conversation_id)
    if (conversation.get("title") or "新对话") == "新对话":
        assistant_message = next(
            (
                str(msg.get("content", ""))
                for msg in reversed(agent_messages)
                if msg.get("role") == "assistant" and msg.get("content")
            ),
            "",
        )
        conversation["title"] = _generate_conversation_title(user_message, assistant_message)

    stored_messages = [
        {"role": msg.get("role"), "content": msg.get("content", "")}
        for msg in agent_messages
        if msg.get("role") in ("user", "assistant") and msg.get("content")
    ]
    conversation["messages"] = stored_messages
    conversation, compressed = maybe_compress_conversation(conversation)
    save_conversation(conversation)
    return conversation, compressed

def build_user_profile_context() -> str:
    """Build stable Summary Memory context for system prompt injection."""
    profile = load_user_profile()
    lines = []
    if profile.get("summary"):
        lines.append(f"用户概况: {profile['summary']}")
    if profile.get("long_term_goals"):
        lines.append("长期目标: " + "；".join(profile["long_term_goals"][:8]))
    if profile.get("research_interests"):
        lines.append("研究兴趣: " + "、".join(profile["research_interests"][:10]))
    if profile.get("active_projects"):
        lines.append("当前项目: " + "、".join(profile["active_projects"][:8]))
    if profile.get("project_preferences"):
        lines.append("项目偏好: " + "；".join(profile["project_preferences"][:8]))
    if profile.get("diet_preferences"):
        lines.append("饮食偏好: " + "；".join(profile["diet_preferences"][:6]))
    if profile.get("agent_policies"):
        policies = [f"{key}={value}" for key, value in profile["agent_policies"].items()]
        lines.append("Agent 策略: " + "；".join(policies[:8]))
    if profile.get("todos"):
        lines.append("长期待办: " + "；".join(profile["todos"][:8]))
    return "\n".join(lines)

def _fallback_update_user_profile(profile: dict, memories: list[dict]) -> dict:
    updated = normalize_user_profile(profile)
    for memory in memories:
        memory_type = memory.get("type", "")
        topic = memory.get("topic", "")
        content = memory.get("content", "")
        if not content:
            continue
        if memory_type == "todo":
            updated["todos"] = _unique_list([*updated["todos"], content], 30)
        elif memory_type == "user_profile":
            if "目标" in topic or "目标" in content or "计划" in content:
                updated["long_term_goals"] = _unique_list([*updated["long_term_goals"], content], 20)
            else:
                updated["summary"] = (updated["summary"] + "\n" + content).strip()[:1200]
        elif memory_type == "preference":
            if "饮食" in topic or "吃" in content:
                updated["diet_preferences"] = _unique_list([*updated["diet_preferences"], content], 20)
            else:
                updated["project_preferences"] = _unique_list([*updated["project_preferences"], content], 30)
        elif memory_type == "project_decision":
            updated["project_preferences"] = _unique_list([*updated["project_preferences"], content], 30)
        elif "论文" in topic or "研究" in topic or "AI" in content or "RAG" in content or "Agent" in content:
            updated["research_interests"] = _unique_list([*updated["research_interests"], content], 20)
    return updated

def update_user_profile_from_memories(memories: list[dict]) -> bool:
    """Merge durable memory items into structured Summary Memory."""
    memories = [m for m in memories or [] if isinstance(m, dict) and m.get("content")]
    if not memories:
        return False

    profile = load_user_profile()
    prompt = f"""
你是「研习助手 Daily Agent」的 Summary Memory 维护器。这个项目是一个本地个人 Agent 工作台，核心模块包括：
- 智能体控制台：自然语言对话、工具调用、权限控制、流式输出。
- 学术动态面板：arXiv 增量检索、收藏夹维护、GitHub 仓库更新监控。
- 文献记忆库：PDF 入库、论文摘要、方向标签、评论、Chroma/RAG 检索。
- 本周待读：待读论文列表、本周已读计数、读完后评论归档。
- 饮食生活：饮食记录、food_rules 规则库、近几天/近 7/30 天趋势分析、未识别食物补充。

请把新抽取的长期记忆合并进现有 Summary Memory。

要求：
1. 只保留稳定、长期有用的信息。
2. 去重、合并同义内容，避免列表越来越啰嗦。
3. 不要保存 API key、密码、token 等敏感信息。
4. 只返回 JSON，不要 Markdown。
5. 必须保持以下字段，且不要新增字段：
{json.dumps(DEFAULT_USER_PROFILE, ensure_ascii=False)}
6. 字段使用规则：
- summary：概括用户对这个 Agent 系统的总体使用习惯或稳定背景，不要写一次性聊天内容。
- long_term_goals：用户长期希望这个系统具备或优化的方向，例如更稳定的记忆、更准确的检索、更好的每日简报。
- research_interests：长期关注的学术方向或检索关键词。
- project_preferences：界面命名、功能行为、模块设计、默认展示方式等稳定偏好。
- agent_policies：确定下来的系统策略，用 key/value 表示，例如 daily_briefing_window、arxiv_empty_fallback、memory_design。
- active_projects：当前正在维护或持续讨论的项目模块，例如 文献记忆库、饮食生活、每日简报。
- diet_preferences：和饮食模块有关的长期偏好或记录规则。
- todos：明确还要做的项目待办。

现有用户画像：
{json.dumps(profile, ensure_ascii=False)}

新长期记忆：
{json.dumps(memories, ensure_ascii=False)}
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{[\s\S]*\}", content)
        merged = json.loads(match.group(0) if match else content)
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 用户画像 LLM 合并失败，使用规则兜底: {e}")
        merged = _fallback_update_user_profile(profile, memories)

    save_user_profile(merged)
    print(f"[\033[92mMemory\033[0m] Summary Memory 已更新: {USER_PROFILE_PATH.name}")
    return True

def build_user_memory_document(content: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    fields = [
        ("Type", metadata.get("type", "")),
        ("Topic", metadata.get("topic", "")),
        ("Content", content),
        ("Source", metadata.get("source", "")),
        ("Created At", metadata.get("created_at", "")),
    ]
    return "\n".join(f"{name}: {value}" for name, value in fields if value)

def store_user_memory(content: str, memory_type: str = "note", topic: str = "", source: str = "manual") -> str:
    """Persist one concise user memory item into Chroma."""
    cleaned = str(content or "").strip()
    if not cleaned:
        return "Error: memory content is empty"

    memory_id = f"mem-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    metadata = {
        "type": str(memory_type or "note").strip()[:40],
        "topic": str(topic or "").strip()[:120],
        "source": str(source or "manual").strip()[:80],
        "created_at": _now_iso(),
    }
    document = build_user_memory_document(cleaned, metadata)
    user_memory_collection.upsert(
        ids=[memory_id],
        documents=[document],
        metadatas=[metadata],
    )
    return f"✅ 已写入用户长期记忆：{cleaned[:120]}"

def search_user_memories(query: str, n_results: int = 5) -> list[dict]:
    """Search persisted user memories."""
    q = str(query or "").strip()
    if not q:
        return []
    try:
        results = user_memory_collection.query(query_texts=[q], n_results=max(1, min(n_results, 10)))
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 用户记忆检索失败: {e}")
        return []

    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0] if results.get("distances") else []
    items = []
    for index, doc in enumerate(documents):
        meta = metadatas[index] if index < len(metadatas) else {}
        items.append({
            "document": doc,
            "metadata": meta,
            "distance": distances[index] if index < len(distances) else None,
        })
    return items

def build_relevant_user_memory_context(query: str, n_results: int = 5) -> str:
    """Build compact memory context for system prompt injection."""
    memories = search_user_memories(query, n_results)
    if not memories:
        return ""

    lines = []
    for item in memories:
        doc = item.get("document", "")
        meta = item.get("metadata", {})
        content = ""
        for line in doc.splitlines():
            if line.startswith("Content:"):
                content = line.replace("Content:", "", 1).strip()
                break
        content = content or doc.replace("\n", " ")[:240]
        label = meta.get("type") or "note"
        topic = meta.get("topic")
        prefix = f"[{label}" + (f"/{topic}" if topic else "") + "]"
        lines.append(f"- {prefix} {content}")
    return "\n".join(lines)

def extract_memories_from_turn(user_message: str, assistant_message: str) -> list[dict]:
    """Use LLM to extract durable memories from one completed chat turn."""
    user_message = str(user_message or "").strip()
    assistant_message = str(assistant_message or "").strip()
    if not user_message or not assistant_message:
        return []

    prompt = f"""
你是「研习助手 Daily Agent」的长期记忆抽取器。这个项目是一个本地个人 Agent 工作台，核心模块包括：
- 智能体控制台：自然语言对话、工具调用、权限控制、流式输出。
- 学术动态面板：arXiv 增量检索、收藏夹维护、GitHub 仓库更新监控。
- 文献记忆库：PDF 入库、论文摘要、方向标签、评论、Chroma/RAG 检索。
- 本周待读：待读论文列表、本周已读计数、读完后评论归档。
- 饮食生活：饮食记录、food_rules 规则库、饮食趋势分析、未识别食物补充。

请从以下一轮对话中提取值得长期保存的信息。

只保存长期有用的信息，例如：
- 用户对上述模块的稳定偏好、目标、长期关注方向
- 对项目架构、功能行为、模块命名、默认策略做出的稳定决策
- 用户明确要求以后遵守的规则，例如检索窗口、fallback 策略、记忆策略、界面展示规则
- 重要待办或项目状态
- 长期关注的学术关键词、文献检索方向或饮食记录规则

不要保存：
- 寒暄、临时错误、一次性问题
- API 密钥、密码、token、隐私敏感原文
- 没有长期价值的普通回答
- 为了回答单个问题而产生的解释性内容，除非它被用户确认成项目设计

只返回 JSON 数组，不要 Markdown。每个元素格式：
{{"type":"preference|project_decision|user_profile|todo|fact","topic":"每日简报|文献记忆库|学术动态面板|本周待读|饮食生活|Memory|MCP|RAG|界面偏好|其他","content":"一条完整、简洁、可独立理解的中文记忆"}}
如果没有值得保存的信息，返回 []。

用户：
{user_message[:3000]}

助手：
{assistant_message[:3000]}
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", content)
        parsed = json.loads(match.group(0) if match else content)
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 对话记忆抽取失败: {e}")
        return []

    memories = []
    if isinstance(parsed, list):
        for item in parsed[:5]:
            if not isinstance(item, dict):
                continue
            memory_content = str(item.get("content") or "").strip()
            if not memory_content:
                continue
            memories.append({
                "type": str(item.get("type") or "note").strip()[:40],
                "topic": str(item.get("topic") or "").strip()[:120],
                "content": memory_content[:600],
            })
    return memories

def remember_conversation_turn(user_message: str, assistant_message: str) -> int:
    """Extract and persist durable memories from a completed conversation turn."""
    memories = extract_memories_from_turn(user_message, assistant_message)
    saved = 0
    for memory in memories:
        result = store_user_memory(
            memory["content"],
            memory_type=memory["type"],
            topic=memory.get("topic", ""),
            source="conversation",
        )
        if not result.startswith("Error:"):
            saved += 1
    if saved:
        print(f"[\033[92mMemory\033[0m] 已从本轮对话写入 {saved} 条用户长期记忆")
        update_user_profile_from_memories(memories)
    return saved

def run_search_user_memory(query: str) -> str:
    memories = search_user_memories(query, n_results=5)
    if not memories:
        return "用户长期记忆中没有找到相关内容。"

    lines = ["从用户长期记忆中检索到："]
    for item in memories:
        meta = item.get("metadata", {})
        doc = item.get("document", "")
        lines.append(
            f"- 类型: {meta.get('type', 'note')} | 主题: {meta.get('topic', '')} | "
            f"时间: {meta.get('created_at', '')}\n  {doc}"
        )
    return "\n".join(lines)

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

def _paper_memory_candidates(query: str, n_results: int = 5) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    try:
        count = paper_collection.count()
        if count <= 0:
            return []
        results = paper_collection.query(
            query_texts=[query],
            n_results=max(1, min(n_results, count)),
        )
    except Exception as e:
        print(f"[\033[33mMemory\033[0m] 文献关联检索失败: {e}")
        return []

    papers = []
    ids = (results.get("ids") or [[]])[0]
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0] if results.get("distances") else []
    for index, paper_id in enumerate(ids):
        metadata = metas[index] if index < len(metas) and metas[index] else {}
        document = docs[index] if index < len(docs) else ""
        papers.append({
            "title": paper_id,
            "summary": metadata.get("summary") or document[:1200],
            "source": metadata.get("source", ""),
            "direction_tags": metadata.get("direction_tags", ""),
            "comment": metadata.get("comment", ""),
            "distance": distances[index] if index < len(distances) else None,
        })
    return papers

def analyze_paper_connections(title: str, abstract: str = "", n_results: int = 5) -> str:
    """Compare a new paper with the personal paper library and produce research links."""
    title = str(title or "").strip()
    abstract = str(abstract or "").strip()
    query = f"{title}\n{abstract}".strip()
    if not query:
        return "Error: 请提供论文标题或摘要。"

    candidates = _paper_memory_candidates(query, n_results)
    if not candidates:
        return "文献记忆库中暂未找到可对照的相关论文。"

    prompt = f"""
你是文献感知型科研分析助手。请把一篇新论文和用户个人文献记忆库中的相关论文做关联分析。

新论文：
标题：{title or "未知"}
摘要/简介：{abstract[:3000] or "无"}

个人文献库候选论文：
{json.dumps(candidates, ensure_ascii=False)}

请用中文输出，控制在 700 字以内，结构如下：
1. 关联论文：列出 2-4 篇最相关论文，说明相似点。
2. 关键差异：说明新论文和已有论文的不同切入点。
3. 思想碰撞：提出 2-3 个可能的组合思路或研究启发。
4. 阅读建议：说明用户是否值得优先读这篇，以及应结合哪几篇旧论文一起读。

要求：只基于给出的新论文信息和候选论文，不要编造不存在的实验结果。
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=40,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: 文献关联分析失败: {e}"

def analyze_research_idea_with_memory(idea: str, n_results: int = 6) -> str:
    """Ground a user's research idea in the personal paper library."""
    idea = str(idea or "").strip()
    if not idea:
        return "Error: 请提供需要分析的研究想法。"

    candidates = _paper_memory_candidates(idea, n_results)
    if not candidates:
        return "文献记忆库中暂未找到足够相关的论文，建议先补充相关方向论文后再做创新性对照。"

    prompt = f"""
你是文献感知型科研分析助手。用户提出了一个研究想法，请结合个人文献记忆库中的论文进行 related work、创新空间和可行路线分析。

用户想法：
{idea[:3000]}

个人文献库候选论文：
{json.dumps(candidates, ensure_ascii=False)}

请用中文输出，结构如下：
1. 相关已有工作：列出最相关论文及其覆盖的思路。
2. 可能的新颖性：指出用户想法中可能区别于已有工作的部分。
3. 风险与重合点：说明哪些地方可能已经被已有论文覆盖。
4. 可尝试的研究路线：给出 2-4 条具体、可执行的实验或方法设计建议。
5. 建议先读：推荐 2-3 篇最应该优先回看的文献。

要求：分析要谨慎，不要夸大创新性；如果证据不足，要明确说明。
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=45,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: 研究想法分析失败: {e}"
# =====================================================================
