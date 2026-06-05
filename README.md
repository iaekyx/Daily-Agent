# ScholarFlow Agent

一个面向个人科研学习场景的本地智能体系统。项目以大模型为任务规划与推理核心，采用 **Memory-Augmented Tool-Calling ReAct** 工作流，通过工具调用连接本地文件系统、ChromaDB 向量数据库、MCP 外部工具和前端交互页面，提供论文发现、阅读管理、文献记忆、研究想法分析、多会话长期记忆与每日科研简报能力。

> 原项目名为 Daily Agent，目前系统定位更偏向个人科研学习与文献工作流，因此也可称为 **ScholarFlow Agent**。

## 项目亮点

- **本地 Agent Harness**：基于 LLM Tool Calling 实现本地 Manager Agent，支持多轮工具调用、权限控制、流式输出、MCP 工具接入和任务分发。
- **Memory-Augmented ReAct Workflow**：在 ReAct-style 工具调用闭环中动态注入会话记忆、用户画像、向量记忆和工具观察结果。
- **Task-aware Context Architecture**：根据任务类型选择性注入 Summary Memory、Vector Memory 和领域 Skill，减少无关记忆干扰。
- **多会话长期记忆**：支持多会话独立存储、自动对话标题生成、长对话摘要压缩、用户画像 Summary Memory 与 ChromaDB Vector Memory。
- **个人文献 RAG**：支持 PDF 解析、中文结构化总结、方向标签自动生成、评论归档、ChromaDB 向量存储、混合检索与 LLM rerank。
- **Literature Connection Skill**：将新论文或用户研究想法与个人文献库中的已有论文进行相似点、差异点、创新空间和组合思路分析。
- **MCP 学术工具**：通过 MCP Server 接入 arXiv 检索、论文收藏管理、GitHub 官方仓库候选召回和仓库更新监控。
- **arXiv 稳定性兜底**：针对 arXiv API 超时和 429 限流问题，实现网页搜索 fallback 与 backoff 策略。
- **WebSocket 实时交互**：前端通过 WebSocket 接收模型回答流式片段、工具调用日志和执行状态。

## 功能模块

### 智能体控制台

用于和 Agent 直接对话。Agent 会根据用户请求动态选择上下文和工具，例如获取当前日期、检索文献库、分析研究想法、读取本地文件、调用 MCP 工具或执行后台任务。

主要能力：

- 多会话切换、新建和删除；
- 长对话自动摘要压缩；
- 根据任务类型动态注入相关长期记忆；
- 模型回答流式输出；
- 工具调用日志实时展示。

<img width="1197" height="776" alt="智能体控制台" src="https://github.com/user-attachments/assets/9f708786-41da-447e-b5f2-b8eeb13bb2c5" />

### 学术动态面板

基于 arXiv MCP 工具检索用户关注方向的新论文，并通过收藏夹工具维护论文与 GitHub 仓库的关联。

主要能力：

- 按用户关注关键词检索最新论文；
- 按上次简报时间进行增量检索；
- 无新增论文时 fallback 返回最新论文；
- 检查收藏论文对应 GitHub 仓库的更新；
- 为缺少代码仓库的论文搜索候选官方仓库。

<img width="1201" height="794" alt="学术动态面板" src="https://github.com/user-attachments/assets/aeb2dc78-4010-4480-be61-ba8ade269d46" />

### 文献记忆库

用于沉淀个人阅读过的论文。上传 PDF 后，系统会自动解析文本、生成中文结构化总结、推荐方向标签，并写入 ChromaDB。

入库流程：

1. 读取 PDF 文本；
2. 调用大模型生成中文结构化总结；
3. 自动推荐方向标签；
4. 尝试提取论文架构图；
5. 构建可检索 document 和 metadata；
6. 写入 ChromaDB `personal_papers` collection；
7. 在前端支持搜索、评论、方向标签、本周待读和删除操作。

检索能力：

- 向量语义检索；
- 标题、摘要、评论、来源、方向标签等搜索范围过滤；
- 关键词与向量混合打分；
- LLM rerank；
- 文献评论和方向标签参与检索。

<img width="1179" height="754" alt="文献记忆库" src="https://github.com/user-attachments/assets/91da3dc2-e9bd-4934-af03-412c654c96c4" />

### 本周待读

管理本周计划阅读的论文，展示待读列表和本周已读计数。读完后可以添加评论，并同步写回文献记忆库。

<img width="1204" height="733" alt="本周待读" src="https://github.com/user-attachments/assets/6673f056-de73-487e-8d76-0caff7f8c0c7" />

### Literature Connection Skill

用于让 Agent 不只是“查论文”，而是基于个人文献库做科研联想和 related work 分析。

触发场景：

- 新论文和文献库已有论文有什么关系；
- 用户提出的研究想法是否有创新空间；
- 每日简报中的新论文是否值得优先阅读；
- 某个方向可以和哪些已读论文产生组合思路；
- 需要梳理 related work、相似点、差异点和重合风险。

标准流程：

```text
新论文 / 用户想法
 -> 检索个人文献库
 -> 分析相似点与差异点
 -> 判断创新空间和重合风险
 -> 给出可尝试路线
 -> 推荐优先阅读论文
```

Skill 文件位于：

```text
agent_runtime/skills/literature_connection.md
```

该 Skill 会按任务类型动态注入 Agent 上下文，不会在普通问答中无意义占用 prompt。

## Agent 架构

项目中的 Agent 不是单轮问答机器人，而是一个轻量级 **Agent Harness**，用于承载 Function Calling 版本的 ReAct workflow。

核心流程：

```text
用户输入
 -> server.py 根据 conversation_id 加载当前会话
 -> conversation summary + recent messages 拼接当前对话上下文
 -> agent.py 根据任务类型构建 Context Policy
 -> 动态注入 Workflow Guidance / Skill / Summary Memory / Vector Memory
 -> LLM 判断直接回答或生成 tool_call
 -> Permission Gate 判断工具风险
 -> Tool Router 调用 Native Tool 或 MCP Tool
 -> 工具结果标准化为 tool observation
 -> LLM 继续推理
 -> 无 tool_call 后输出最终回答
 -> 保存当前会话、压缩长上下文、抽取长期记忆
```

核心设计：

- **Manager Agent Pattern**：主 Agent 负责理解意图、选择工具和汇总结果。
- **Tool Router / Command Pattern**：Native Tools 和 MCP Tools 被统一注册为工具池。
- **MCP Adapter Pattern**：MCP Server 工具被转换为 `mcp__{server}__{tool}` 格式注入 Agent。
- **Permission Guard**：工具调用前进行 read/write/high risk 分级。
- **Memory Repository**：对话、用户画像、长期记忆和文献库分别持久化。
- **RAG Pipeline**：PDF 解析、总结、标签、入库、检索、rerank 形成完整流水线。
- **WebSocket Observer**：模型输出、工具日志和状态通过 WebSocket 推送到前端。

## 记忆系统

项目包含三类记忆：

### Conversation Memory

每个对话独立存储在：

```text
data/conversations.json
```

支持：

- 多会话独立保存；
- 自动对话标题生成；
- 最近消息保留；
- 超过 `MAX_ACTIVE_MESSAGES = 16` 后自动摘要压缩；
- 压缩摘要和最近消息共同作为后续上下文。

### Summary Memory

结构化用户画像存储在：

```text
data/user_profile.json
```

用于保存稳定偏好、研究兴趣、项目决策、长期目标、Agent 策略等。

### Vector Memory

长期记忆片段存储在 ChromaDB：

```text
vector_db/
```

包括：

- `user_memory`：用户长期记忆；
- `personal_papers`：个人文献库。

Agent 回答前会根据当前任务类型和用户输入选择是否检索并注入相关记忆。

## MCP 工具

项目通过 `.claude-plugin/plugin.json` 注册两个 MCP Server。

### arXiv MCP

文件：

```text
arxiv_mcp_server.py
```

工具：

- `search_arxiv`

能力：

- 按关键词检索 arXiv；
- 支持 `published_after` / `published_before` 增量检索；
- 支持 `fallback_latest_on_empty`；
- 返回论文标题、发布日期、作者、链接和摘要；
- API 超时或 429 限流时自动降级到 arXiv 搜索网页解析；
- 对 API 失败设置 backoff，避免短时间内反复请求导致持续阻塞。

### Collector MCP

文件：

```text
collection_mcp_server.py
```

工具：

- `get_missing_repo_candidates`：为收藏夹中没有 GitHub 仓库的论文搜索候选代码仓库；
- `check_repo_updates`：检查已绑定 GitHub 仓库的论文是否有新提交；
- `save_article`：将论文保存到收藏夹；
- `update_paper_repo`：将确认后的官方仓库写回收藏夹。

## 技术栈

- Python
- FastAPI
- WebSocket
- OpenAI-compatible Chat Completions API
- Gemini / DashScope Qwen
- ChromaDB
- pypdf / PyMuPDF
- MCP JSON-RPC
- Vanilla HTML / CSS / JavaScript

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/iaekyx/Daily-Agent.git
cd Daily-Agent
```

### 2. 安装依赖

推荐使用 Python 3.10+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果你使用本地 Conda 环境，也可以直接使用对应 Python 启动，例如：

```bash
/Users/zcy/anaconda3/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

### 3. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

然后在 `.env` 中填写自己的密钥：

```env
GEMINI_API_KEY=
DASHSCOPE_API_KEY=
MODEL_ID=
GITHUB_TOKEN=
```

如果同时配置了 `GEMINI_API_KEY` 和 `DASHSCOPE_API_KEY`，系统会优先使用 Gemini。

### 4. 启动服务

推荐使用：

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

访问：

```text
http://localhost:8000/frontend/index.html
```

也可以访问：

```text
http://localhost:8000
```

后端会返回前端首页。

## 数据与隐私

本项目默认将个人运行数据保存在本地，例如：

- `.env`
- `favorites.json`
- `agent_status.json`
- `data/conversations.json`
- `data/user_profile.json`
- `data/reading_queue.json`
- `vector_db/`
- 上传的 PDF 文件

这些文件应避免提交到公开仓库。上传 GitHub 前请确认没有把真实 API Key、私人论文 PDF、个人对话记录或向量数据库提交进去。

## 目录结构

```text
.
├── agent.py                    # Agent 主循环、native tools、Manager/Worker 调度
├── server.py                   # FastAPI 后端、WebSocket、前端 API、每日简报触发
├── scheduler.py                # 后台任务调度器
├── arxiv_mcp_server.py         # arXiv MCP Server
├── collection_mcp_server.py    # 收藏夹/GitHub MCP Server
├── agent_runtime/
│   ├── config.py               # LLM 客户端配置
│   ├── memory.py               # 文献入库、RAG 检索、多层记忆、文献联想分析
│   ├── mcp.py                  # MCP Client 与工具路由
│   ├── permissions.py          # 工具权限控制
│   ├── skills/
│   │   └── literature_connection.md
│   └── tasks.py                # 后台任务状态管理
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── data/                       # 本地运行数据，通常不提交
├── vector_db/                  # ChromaDB 持久化目录，通常不提交
├── .claude-plugin/
│   └── plugin.json             # MCP 插件配置
├── .env.example                # 环境变量示例
├── requirements.txt
└── README.md
```

## 后续规划

- 增加轻量级 Run State，持久化每次 Agent 任务的状态、工具调用轨迹和失败原因。
- 为 Literature Connection Skill 增加小规模评测集，评估文献库调用率、结构化输出完整率和研究建议质量。
- 接入更多学术数据源，例如 Semantic Scholar、OpenAlex 或 Papers with Code。
- 增强文献关联图谱能力，支持按方向标签、方法模块和用户评论进行论文聚类。
- 完善自动化测试和一键初始化脚本。
