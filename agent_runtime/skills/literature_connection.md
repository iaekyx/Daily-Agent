# Literature Connection Skill

## Purpose

Use this skill when the user needs research-oriented reasoning that connects a new paper, a daily briefing paper, or a research idea with the user's personal paper knowledge base.

The goal is not only to retrieve papers, but to produce paper-to-paper and idea-to-paper connections:

- Which stored papers are related?
- What are the similarities and differences?
- What ideas can be combined?
- Where may the user's idea overlap with existing work?
- What should the user read first?

## Trigger Conditions

Activate this skill when the user asks about any of the following:

- A new paper's novelty, value, relation to previous papers, or reading priority.
- A daily research briefing that includes new arXiv papers.
- A research idea, method proposal, experiment direction, or possible topic.
- Related work, innovation space, overlap risk, or literature review.
- Questions like "这个想法怎么样", "有没有创新性", "和我文献库里哪些论文相关", "能不能产生一些组合思路".

Do not activate this skill for simple date questions, UI questions, food/life records, or generic chat.

## Required Tools

Prefer these tools:

1. `analyze_paper_connections`
   - Use when there is a concrete paper title, abstract, or new paper summary.
   - It compares the new paper with papers stored in the personal paper knowledge base.

2. `analyze_research_idea`
   - Use when the user proposes a research idea or asks whether an idea is novel.
   - It grounds the idea in the personal paper knowledge base.

3. `search_memory`
   - Use when the user only asks for related stored papers or when more paper evidence is needed.

4. arXiv MCP `search_arxiv`
   - Use only when the user explicitly asks for newest/external related work, or when local evidence is insufficient and external recall is necessary.
   - Treat arXiv as an external candidate retriever, not as a full-paper reader.

## Workflow

For a new paper:

1. Extract the paper title and abstract/core idea from the user input or arXiv result.
2. Call `analyze_paper_connections`.
3. If the analysis says the local paper library has no related papers and the user needs broader related work, call arXiv search.
4. Produce a concise synthesis grounded in the returned evidence.

For a research idea:

1. Restate the idea in one sentence.
2. Call `analyze_research_idea`.
3. If local evidence is weak and the user asks about novelty/latest work, call arXiv search for external candidates.
4. Compare local evidence and external candidates separately.
5. Give cautious novelty judgment and concrete next steps.

For a daily briefing:

1. After new papers are retrieved, select at most 3 papers that are most relevant or potentially valuable.
2. For each selected paper, call `analyze_paper_connections`.
3. Add a "与文献记忆库的关联" section to the briefing.
4. If no related local papers are found, say so directly instead of inventing connections.

## Output Structure

Use the following structure when the user wants analysis:

### 相关已有工作

List the most relevant stored papers or external candidates. Briefly state why each is relevant.

### 相似点与差异点

Compare the new paper or idea against the related papers.

### 可能的创新空间

Identify what may still be new, underexplored, or combinable.

### 风险与重合点

Point out where the idea may already overlap with existing work.

### 可尝试路线

Give concrete experiments, method combinations, evaluation directions, or next reading actions.

### 建议先读

Recommend 2-3 papers from the personal library or external search results.

## Grounding Rules

- Do not claim novelty only from model knowledge.
- Separate local-library evidence from external arXiv evidence.
- If only titles/abstracts are available, say the analysis is preliminary.
- Do not invent paper results, datasets, numbers, or GitHub repositories.
- If the local paper library has no relevant papers, say that clearly and suggest adding related papers to the reading queue or paper memory.
- Prefer cautious wording: "可能", "初步看", "需要进一步读全文确认".

## Good Final Answer Style

Keep the answer useful for research decisions. Avoid generic praise. The best answer should help the user decide:

- Whether this paper is worth reading.
- Which stored papers to compare with.
- Whether the idea is already covered.
- What concrete experiment or method combination to try next.
