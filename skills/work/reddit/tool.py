from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.work.reddit import reddit

OPERATIONS = {
    "latest_posts": reddit.latest_posts,
    "search_subreddit": reddit.search_subreddit,
    "search_all": reddit.search_all,
    "get_post": reddit.get_post,
    "subreddit_info": reddit.subreddit_info,
    "top_posts": reddit.top_posts,
}
SUBREDDIT_OPERATIONS = {"latest_posts", "top_posts", "search_subreddit", "subreddit_info"}
POST_FEED_OPERATIONS = {"latest_posts", "top_posts", "search_subreddit", "search_all"}

MAX_TEXT_CHARS = 5000


def _normalize_args(args: dict | str | None) -> dict[str, Any]:
    if args is None:
        return {}

    if isinstance(args, dict):
        return args

    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"args must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("args JSON must deserialize to an object.")
        return parsed

    raise ValueError("args must be a dict, JSON string, or null.")


def _ok(operation: str | None, result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": operation,
        "result": result,
        "error": None,
    }


def _fail(operation: str | None, error: str, recovery_hint: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "result": None,
        "error": error,
    }


def _error_hint_for_error(operation: str | None, message: str) -> str:
    if not operation:
        return "Retry with operation and payload. Supported operations: latest_posts, top_posts, search_subreddit, search_all, get_post, subreddit_info."

    if operation in {"latest_posts", "top_posts"}:
        return "Retry with payload.subreddit (single) or payload.subreddits (list), and payload.limit <= 100."

    if operation == "search_subreddit":
        return "Retry with payload.subreddit (single) or payload.subreddits (list), plus payload.query."

    if operation == "get_post":
        return "Use a valid Reddit post permalink containing '/comments/'. If you only have subreddit names, call latest_posts or top_posts."

    if operation == "subreddit_info":
        return "Retry with payload.subreddit (single) or payload.subreddits (list)."

    if "403" in message:
        return "Try a different Reddit post URL, or call latest_posts/top_posts and use enriched post details."

    return "Retry with valid payload fields for the requested operation."


def _bind_subreddit_alias(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation not in SUBREDDIT_OPERATIONS:
        return payload

    has_subreddit = "subreddit" in payload
    has_subreddits = "subreddits" in payload

    if has_subreddit and has_subreddits:
        raise ValueError("Use either payload.subreddit or payload.subreddits, not both.")

    if has_subreddits:
        mapped = dict(payload)
        mapped["subreddit"] = mapped.pop("subreddits")
        return mapped

    return payload


def _clean_text(value: Any, max_len: int = MAX_TEXT_CHARS) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


def _format_subreddit_value(value: Any) -> str:
    if isinstance(value, list):
        cleaned = [f"r/{_clean_text(v, 64)}" for v in value if _clean_text(v, 64)]
        return ", ".join(cleaned) if cleaned else "n/a"
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        cleaned = [f"r/{_clean_text(p, 64)}" for p in parts]
        return ", ".join(cleaned) if cleaned else "n/a"
    return "n/a"


def _extract_post_groups(result: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    if not isinstance(result, dict):
        return []

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    multi = result.get("subreddits")
    if isinstance(multi, list) and multi and all(isinstance(item, dict) for item in multi):
        if any("posts" in item for item in multi):
            for item in multi:
                posts = item.get("posts")
                if isinstance(posts, list):
                    sub_name = _clean_text(item.get("subreddit") or item.get("display_name") or "unknown", 64)
                    groups.append((sub_name, [p for p in posts if isinstance(p, dict)]))
            if groups:
                return groups

    posts = result.get("posts")
    if isinstance(posts, list):
        sub_name = _clean_text(result.get("subreddit") or result.get("mode") or "all", 64)
        groups.append((sub_name, [p for p in posts if isinstance(p, dict)]))
    return groups


def _post_excerpt(post: dict[str, Any]) -> str:
    selftext = post.get("selftext")
    if isinstance(selftext, str) and selftext.strip():
        return _clean_text(selftext)

    details = post.get("post_details")
    if isinstance(details, dict):
        detail_selftext = details.get("selftext")
        if isinstance(detail_selftext, str) and detail_selftext.strip():
            return _clean_text(detail_selftext)
    return ""


def _post_comments(post: dict[str, Any]) -> list[dict[str, Any]]:
    comments = post.get("top_comments")
    if isinstance(comments, list):
        return [c for c in comments if isinstance(c, dict)]
    return []


def _build_post_feed_markdown(operation: str, payload: dict[str, Any], result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Reddit Retrieval Input")
    lines.append("")
    lines.append("## Request")
    lines.append(f"- operation: `{operation}`")
    if "subreddits" in payload:
        lines.append(f"- subreddits: { _format_subreddit_value(payload.get('subreddits')) }")
    elif "subreddit" in payload:
        lines.append(f"- subreddit: { _format_subreddit_value(payload.get('subreddit')) }")
    if "query" in payload:
        lines.append(f"- query: {_clean_text(payload.get('query'), 180)}")
    if "limit" in payload:
        lines.append(f"- limit: {_as_int(payload.get('limit'), 0)}")
    if "time_filter" in payload:
        lines.append(f"- time_filter: {_clean_text(payload.get('time_filter'), 32)}")
    if "sort" in payload:
        lines.append(f"- sort: {_clean_text(payload.get('sort'), 32)}")
    lines.append("")
    lines.append("## Retrieved Posts")

    groups = _extract_post_groups(result)
    if not groups:
        lines.append("- No posts returned.")
        return "\n".join(lines)

    for subreddit_name, posts in groups:
        lines.append(f"### r/{_clean_text(subreddit_name, 64)}")
        if not posts:
            lines.append("- No posts returned.")
            continue
        for idx, post in enumerate(posts, start=1):
            title = _clean_text(post.get("title"), 500) or "Untitled"
            permalink = post.get("permalink") or post.get("url") or ""
            score = _as_int(post.get("score"), 0)
            comments = _as_int(post.get("num_comments"), 0)
            lines.append(f"{idx}. **{title}**")
            lines.append(f"   - score: {score} | comments: {comments}")
            if permalink:
                lines.append(f"   - link: {permalink}")
            body = _post_excerpt(post)
            if body:
                lines.append(f"   - body: {body}")
            returned_comments = _post_comments(post)
            lines.append(f"   - returned_comments: {len(returned_comments)}")
            if returned_comments:
                lines.append("   - comments:")
                for cidx, comment in enumerate(returned_comments, start=1):
                    cscore = _as_int(comment.get("score"), 0)
                    cauthor = _clean_text(comment.get("author"), 120) or "unknown"
                    cbody = _clean_text(comment.get("body"), 5000)
                    clink = comment.get("permalink")
                    lines.append(f"     {cidx}. score={cscore} author={cauthor}")
                    if cbody:
                        lines.append(f"        - body: {cbody}")
                    if clink:
                        lines.append(f"        - link: {clink}")
        lines.append("")

    return "\n".join(lines)


def _build_get_post_markdown(payload: dict[str, Any], result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Reddit Retrieval Input")
    lines.append("")
    lines.append("## Request")
    lines.append("- operation: `get_post`")
    lines.append(f"- url: {_clean_text(payload.get('url'), 280)}")
    lines.append(f"- comment_sort: {_clean_text(payload.get('comment_sort') or 'top', 24)}")
    lines.append(f"- comment_limit: {_as_int(payload.get('comment_limit'), 0)}")
    lines.append("")

    post = result.get("post", {}) if isinstance(result, dict) else {}
    comments = result.get("comments", []) if isinstance(result, dict) else []
    if not isinstance(post, dict):
        post = {}
    if not isinstance(comments, list):
        comments = []

    title = _clean_text(post.get("title"), 500) or "Untitled"
    subreddit = _clean_text(post.get("subreddit"), 64)
    lines.append("## Post")
    lines.append(f"- title: {title}")
    if subreddit:
        lines.append(f"- subreddit: r/{subreddit}")
    lines.append(f"- score: {_as_int(post.get('score'), 0)}")
    lines.append(f"- comments: {_as_int(post.get('num_comments'), 0)}")
    if post.get("permalink"):
        lines.append(f"- permalink: {post.get('permalink')}")
    body = _clean_text(post.get("selftext"), 5000)
    if body:
        lines.append(f"- selftext: {body}")
    lines.append("")

    lines.append("## Returned Comments")
    valid_comments = [c for c in comments if isinstance(c, dict)]
    lines.append(f"- count: {len(valid_comments)}")
    if not valid_comments:
        lines.append("- No comments returned.")
    else:
        for idx, comment in enumerate(valid_comments, start=1):
            lines.append(f"{idx}. score={_as_int(comment.get('score'), 0)}")
            lines.append(f"   - author: {_clean_text(comment.get('author'), 120) or 'unknown'}")
            lines.append(f"   - body: {_clean_text(comment.get('body'), 5000)}")
            if comment.get("permalink"):
                lines.append(f"   - link: {comment.get('permalink')}")

    return "\n".join(lines)


def _build_subreddit_info_markdown(payload: dict[str, Any], result: Any) -> str:
    lines: list[str] = []
    lines.append("# Reddit Retrieval Input")
    lines.append("")
    lines.append("## Request")
    lines.append("- operation: `subreddit_info`")
    if "subreddits" in payload:
        lines.append(f"- subreddits: {_format_subreddit_value(payload.get('subreddits'))}")
    elif "subreddit" in payload:
        lines.append(f"- subreddit: {_format_subreddit_value(payload.get('subreddit'))}")
    lines.append("")
    lines.append("## Subreddit Metadata")

    if isinstance(result, dict) and isinstance(result.get("subreddits"), list):
        items = [s for s in result["subreddits"] if isinstance(s, dict)]
    elif isinstance(result, dict):
        items = [result]
    else:
        items = []

    if not items:
        lines.append("- No subreddit metadata returned.")
        return "\n".join(lines)

    for item in items:
        name = _clean_text(item.get("display_name"), 64) or "unknown"
        lines.append(f"### r/{name}")
        lines.append(f"- subscribers: {_as_int(item.get('subscribers'), 0)}")
        lines.append(f"- active_user_count: {_as_int(item.get('active_user_count'), 0)}")
        if item.get("title"):
            lines.append(f"- title: {_clean_text(item.get('title'), 180)}")
        if item.get("public_description"):
            lines.append(f"- public_description: {_clean_text(item.get('public_description'))}")
        if item.get("url"):
            lines.append(f"- link: {item.get('url')}")
        lines.append("")

    return "\n".join(lines)


def _build_markdown_context(operation: str, payload: dict[str, Any], result: Any) -> str:
    if not isinstance(result, dict):
        return (
            "# Reddit Retrieval Input\n\n"
            "## Request\n"
            f"- operation: `{operation}`\n\n"
            "## Raw Result\n"
            f"- {_clean_text(result, 1200)}"
        )

    if operation in POST_FEED_OPERATIONS:
        context = _build_post_feed_markdown(operation, payload, result)
    elif operation == "get_post":
        context = _build_get_post_markdown(payload, result)
    elif operation == "subreddit_info":
        context = _build_subreddit_info_markdown(payload, result)
    else:
        context = (
            "# Reddit Retrieval Input\n\n"
            f"## Request\n- operation: `{operation}`\n\n"
            f"## Result Snapshot\n- {_clean_text(result, 1200)}"
        )

    return context


def _prepare_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    tuned = dict(payload)

    if operation in {"latest_posts", "top_posts"}:
        # Favor richer post/comment payloads for markdown output.
        tuned.setdefault("sparse_enrich_threshold", 30)
        tuned.setdefault("sparse_comment_limit", 25)
        tuned.setdefault("sparse_comment_sort", "top")

    if operation == "get_post":
        tuned.setdefault("comment_limit", 100)
        tuned.setdefault("comment_sort", "top")

    return tuned


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    del extra_args
    operation: str | None = None

    try:
        parsed_args = _normalize_args(args)
        operation = parsed_args.get("operation")
        payload = parsed_args.get("payload", {})

        if not operation:
            raise ValueError("operation is required.")

        if operation not in OPERATIONS:
            supported = ", ".join(sorted(OPERATIONS))
            raise ValueError(f"Unknown operation: {operation}. Supported: {supported}.")

        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")
        payload = _bind_subreddit_alias(operation, payload)
        payload = _prepare_payload(operation, payload)

        func = OPERATIONS[operation]

        # PRAW calls are blocking; run them in a worker thread to keep async caller responsive.
        raw_result = await asyncio.to_thread(func, **payload)
        markdown_result = await asyncio.to_thread(_build_markdown_context, operation, payload, raw_result)
        return _ok(
            operation,
            {
                "markdown": markdown_result,
            },
        )

    except Exception as exc:
        message = str(exc)
        return _fail(operation, message, _error_hint_for_error(operation, message))


def main() -> None:
    cases = [
        {"name": "latest_posts", "args": {"operation": "latest_posts", "payload": {"subreddit": "", "limit": 1}}},
        {"name": "top_posts",
         "args": {"operation": "top_posts", "payload": {"subreddit": "", "limit": 1, "time_filter": "day"}}},
        {"name": "search_subreddit",
         "args": {"operation": "search_subreddit", "payload": {"subreddit": "python", "query": "", "limit": 1}}},
        {"name": "search_all", "args": {"operation": "search_all", "payload": {"query": "", "limit": 1}}},
        {"name": "subreddit_info", "args": {"operation": "subreddit_info", "payload": {"subreddit": ""}}},
        {"name": "get_post",
         "args": {"operation": "get_post", "payload": {"url": "https://www.reddit.com/r/python/", "comment_limit": 1}}},
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(args=case["args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
