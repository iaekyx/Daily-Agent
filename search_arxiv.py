import arxiv

# 构造搜索查询
# arXiv 的 API 使用 Lucene 查询语法
# 搜索最近一个月发表的论文
search = arxiv.Search(
    query = 'abs:"AI-Generated Image Detection"',
    max_results = 5,
    sort_by = arxiv.SortCriterion.SubmittedDate,
    sort_order = arxiv.SortOrder.Descending
)

for result in search.results():
    print(f"Title: {result.title}")
    print(f"Authors: {', '.join([author.name for author in result.authors])}")
    print(f"Link: {result.entry_id}")
    print(f"Summary: {result.summary[:300]}...")
    print("-" * 20)
