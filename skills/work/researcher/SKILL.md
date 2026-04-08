---
name: researcher
description: >-
  Research external information and produce concise synthesized summaries.
  Use when answers depend on up-to-date web sources beyond local files.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "2.0"
---

# Researcher Skill

## When to Use
- Use when answers require external web information.
- Use `slow` mode for deeper synthesis and `fast` mode for quick metadata.

## Procedure
1. Write a precise `query` including scope and timeframe.
2. Choose `mode` (`slow` or `fast`).
3. Run the query once.
4. Narrow and rerun if coverage is weak.
5. Return concise synthesized findings.

## Pitfalls
- Missing `query`.
- Invalid mode value.
- Broad query producing noisy output.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="researcher",
  script_name="tool.py",
  args={
    "query": "latest updates in lithium battery recycling policy in India",
    "mode": "slow"
  }
)
```

Other major operation (short form):
- fast mode: `run_skill_script(..., args={"query":"open source ai browser tools","mode":"fast"})`

## Verification
- Call returns `ok=true`.
- Output directly answers the research objective.
- Follow-up query improves weak coverage.
