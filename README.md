# 研习助手 Daily Agent

一个面向个人科研学习与生活记录的本地 Agent 系统。项目以大模型为推理核心，通过工具调用连接本地文件、文献向量库、MCP 插件、阅读计划和饮食记录，提供学术动态追踪、论文记忆检索、每日简报和生活数据辅助分析等能力。

## 项目亮点

- **Manager Agent 架构**：主 Agent 负责理解用户意图、规划任务、调用工具和汇总结果。
- **多工具调用闭环**：支持文件读写、命令执行、时间获取、文献入库、语义检索、饮食管理等 native tools。
- **MCP 插件扩展**：通过 MCP Server 接入 arXiv 检索和论文收藏夹维护能力。
- **个人文献记忆库**：上传 PDF 后自动提取文本、生成中文总结、提取方向标签，并写入 Chroma 向量数据库。
- **混合检索与 rerank**：文献库搜索结合向量语义检索、关键词匹配、搜索范围过滤和模型 rerank。
- **每日主动简报**：每日首次启动时自动生成学术动态、本周待读和近三天饮食建议。
- **生活与饮食分析**：基于本地轻量食物规则库分析近期饮食结构，并支持用户新增食物规则。
- **WebSocket 流式输出**：前端通过 WebSocket 接收 Agent 的实时回复片段。
- **权限控制**：对本地工具调用做读写/高风险分级，降低本地 Agent 误操作风险。

## 功能模块

### 智能体控制台

用于和 Agent 直接对话。Agent 可以根据问题自动决定是否调用工具，例如获取当前日期、查询历史论文、记录饮食、读取本地文件或执行后台任务。

<img width="1197" height="776" alt="截屏2026-06-01 19 25 48" src="https://github.com/user-attachments/assets/9f708786-41da-447e-b5f2-b8eeb13bb2c5" />

### 学术动态面板

基于 arXiv MCP 工具检索用户关注方向的新论文，并通过收藏夹工具维护论文与 GitHub 仓库的关联。

<img width="1201" height="794" alt="截屏2026-06-01 19 10 37" src="https://github.com/user-attachments/assets/aeb2dc78-4010-4480-be61-ba8ade269d46" />


### 文献记忆库

支持上传 PDF 论文。系统会：

1. 读取 PDF 文本；
2. 调用大模型生成中文结构化总结；
3. 自动推荐方向标签；
4. 尝试提取论文架构图；
5. 将检索用 document 和 metadata 写入 Chroma 向量数据库；
6. 在前端提供搜索、评论、方向标签和本周待读操作。
   
<img width="1179" height="754" alt="截屏2026-06-01 19 11 59" src="https://github.com/user-attachments/assets/91da3dc2-e9bd-4934-af03-412c654c96c4" />

### 本周待读

管理本周计划阅读的论文，展示待读列表和本周已读计数。读完后可以添加评论，并同步写回文献记忆库。

<img width="1204" height="733" alt="截屏2026-06-01 19 12 33" src="https://github.com/user-attachments/assets/6673f056-de73-487e-8d76-0caff7f8c0c7" />


### 饮食生活

记录每日饮食内容，不再局限于固定菜单。系统会结合 `data/food_rules.json` 中的本地食物规则，分析近期饮食标签、健康倾向和未识别食物，并支持用大模型为新食物生成标签规则。

<img width="1186" height="791" alt="截屏2026-06-01 19 19 27" src="https://github.com/user-attachments/assets/99db1083-00e6-480b-889e-f68aa37a8058" />

## Agent 架构

项目中的 Agent 不是单轮聊天机器人，而是一个 **LLM + Tool Calling + Execution Loop** 的本地智能体。

核心流程：

1. 前端通过 WebSocket 将用户消息发送给 FastAPI 后端；
2. 后端将消息追加到会话历史；
3. `agent_loop` 构建 system prompt 和工具池；
4. 大模型判断直接回答或调用工具；
5. 工具执行结果作为 `tool` 消息回写上下文；
6. Agent 再次调用大模型继续推理；
7. 无需更多工具后，最终回答通过 WebSocket 返回前端。

核心文件：

- `agent.py`：Agent 主循环、native tools、Manager/Worker 调度逻辑。
- `server.py`：FastAPI 后端、WebSocket 通信、前端 API、每日简报触发。
- `agent_runtime/config.py`：大模型客户端配置，支持 Gemini 和 DashScope/Qwen。
- `agent_runtime/memory.py`：PDF 入库、文献总结、方向标签、Chroma 检索。
- `agent_runtime/mcp.py`：MCP Client、插件扫描、工具路由。
- `agent_runtime/permissions.py`：工具调用权限控制。
- `scheduler.py`：后台流水线任务调度。

## MCP 工具

项目通过 `.claude-plugin/plugin.json` 注册了两个 MCP Server。

### arXiv MCP

文件：`arxiv_mcp_server.py`

提供工具：

- `search_arxiv`

作用是根据关键词调用 arXiv 官方 API，返回论文标题、发布日期、作者、链接和摘要。它主要服务于学术动态检索和每日简报。

### Collector MCP

文件：`collection_mcp_server.py`

提供工具：

- `get_missing_repo_candidates`：为收藏夹中没有 GitHub 仓库的论文搜索候选代码仓库。
- `check_repo_updates`：检查已绑定 GitHub 仓库的论文是否有新提交。
- `save_article`：将论文保存到收藏夹。
- `update_paper_repo`：将确认后的官方仓库写回收藏夹。

这些 MCP 工具会在 Agent 启动时被转换成 `mcp__{server}__{tool}` 格式，并注入到大模型工具池中。

## 技术栈

- Python
- FastAPI
- WebSocket
- OpenAI-compatible Chat Completions API
- Gemini / DashScope Qwen
- ChromaDB
- PyPDF / PyMuPDF
- MCP JSON-RPC
- Vanilla HTML/CSS/JavaScript

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd daily-agent
```

### 2. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

```bash
python3 server.py
```

浏览器访问：

```text
http://localhost:8000
```

## 数据与隐私

本项目默认将个人运行数据保存在本地，例如：

- `.env`
- `favorites.json`
- `meals.json`
- `reading_queue.json`
- `agent_status.json`
- `vector_db/`
- 上传的 PDF 文件

这些文件已经在 `.gitignore` 中忽略，不建议提交到公开仓库。上传 GitHub 前请确认没有把真实 API Key、私人论文 PDF 或个人饮食记录提交进去。

## 目录结构

```text
.
├── agent.py                    # Agent 主体与工具调用循环
├── server.py                   # FastAPI 后端与 WebSocket 服务
├── scheduler.py                # 后台任务调度器
├── arxiv_mcp_server.py         # arXiv MCP Server
├── collection_mcp_server.py    # 收藏夹/GitHub MCP Server
├── agent_runtime/
│   ├── config.py               # LLM 客户端配置
│   ├── memory.py               # 文献入库与检索
│   ├── mcp.py                  # MCP 客户端与工具路由
│   ├── permissions.py          # 工具权限控制
│   └── tasks.py                # 后台任务状态管理
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── data/
│   └── food_rules.json         # 本地食物规则库
├── .claude-plugin/
│   └── plugin.json             # MCP 插件配置
├── .env.example                # 环境变量示例
└── requirements.txt
```

## 后续规划

- 将文献处理能力进一步抽象为独立 Paper Insight Skill。
- 增强阅读计划推荐，根据方向标签和历史兴趣排序本周待读。
- 支持更丰富的营养估算字段和长期饮食趋势分析。
- 增加更完整的测试和一键初始化脚本。
- 支持更多学术数据源，例如 Semantic Scholar、OpenAlex 等。

