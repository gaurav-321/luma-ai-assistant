---
name: reddit
description: >-
  Retrieve and summarize Reddit posts, comments, and subreddit metadata.
  Use for subreddit monitoring, discussion analysis, and post-level context extraction.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "2.0"
---

# Reddit Skill

## When to Use
- Use for subreddit monitoring, post retrieval, and comment-context analysis.
- Use when Reddit-specific evidence is needed in markdown form.

## Procedure
1. Choose operation by task type (`latest_posts`, `search_subreddit`, `get_post`, etc.).
2. Prepare payload with valid subreddit/query/url fields.
3. Execute and read `result.markdown`.
4. Refine filters and rerun if needed.
5. Summarize from returned markdown evidence.

## Pitfalls
- Passing both `subreddit` and `subreddits`.
- Invalid permalink for `get_post`.
- Expecting raw JSON instead of markdown output.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="reddit",
  script_name="tool.py",
  args={
    "operation": "latest_posts",
    "payload": {
      "subreddit": "LocalLLaMA",
      "limit": 10
    }
  }
)
```

Then targeted search:

```python
run_skill_script(
  skill_name="reddit",
  script_name="tool.py",
  args={
    "operation": "search_subreddit",
    "payload": {"subreddit": "python", "query": "asyncio", "limit": 5}
  }
)
```

Other major operations (short form):
- `top_posts`: `run_skill_script(..., args={"operation":"top_posts","payload":{"subreddit":"python","limit":5,"time_filter":"day"}})`
- `search_all`: `run_skill_script(..., args={"operation":"search_all","payload":{"query":"agent frameworks","limit":10}})`
- `get_post`: `run_skill_script(..., args={"operation":"get_post","payload":{"url":"https://www.reddit.com/r/python/comments/.../","comment_limit":30}})`
- `subreddit_info`: `run_skill_script(..., args={"operation":"subreddit_info","payload":{"subreddits":["python","learnpython"]}})`

## Verification
- Each call returns `ok=true`.
- `result.markdown` contains requested entities.
- Refinement improves relevance where needed.
