from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import praw
from prawcore.exceptions import Forbidden, NotFound, Redirect

from core.utils.config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET

VALID_TOP_TIME_FILTERS = {"all", "day", "hour", "month", "week", "year"}
VALID_SEARCH_TIME_FILTERS = {"all", "day", "hour", "month", "week", "year"}
VALID_SEARCH_SORTS = {"relevance", "hot", "top", "new", "comments"}
VALID_COMMENT_SORTS = {"confidence", "top", "new", "controversial", "old", "qa"}

MAX_LISTING_LIMIT = 100
MAX_WORKERS = 4
DEFAULT_SPARSE_ENRICH_THRESHOLD = 15
DEFAULT_SPARSE_COMMENT_LIMIT = 10


def _require_text(value: Any, field: str, max_len: int | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    if max_len is not None:
        return text[:max_len]
    return text


def _require_int(value: Any, field: str, min_value: int | None = None, max_value: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be int")

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be int") from exc

    if min_value is not None and number < min_value:
        raise ValueError(f"{field} must be >= {min_value}")

    if max_value is not None and number > max_value:
        raise ValueError(f"{field} must be <= {max_value}")

    return number


def _validate_choice(value: Any, field: str, allowed: set[str], default: str) -> str:
    if value is None:
        return default

    text = str(value).strip().lower()
    if text not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return text


def _normalize_subreddit_name(value: Any) -> str:
    name = _require_text(value, "subreddit", max_len=128)
    if name.startswith("r/"):
        name = name[2:]
    name = name.strip("/")
    if not name:
        raise ValueError("subreddit is required")
    if " " in name:
        raise ValueError("subreddit must not contain spaces")
    return name


def _normalize_subreddits(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [segment.strip() for segment in value.split(",") if segment.strip()]
        if not raw:
            raise ValueError("subreddit is required")
        return [_normalize_subreddit_name(item) for item in raw]

    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("subreddit list must not be empty")
        return [_normalize_subreddit_name(item) for item in value]

    raise ValueError("subreddit must be a string or list of strings")


def _build_reddit_client() -> praw.Reddit:
    client_id = REDDIT_CLIENT_ID
    client_secret = REDDIT_CLIENT_SECRET
    user_agent = "openclaw-reddit-skill/1.1"

    if not client_id:
        raise ValueError("REDDIT_CLIENT_ID is required")
    if not client_secret:
        raise ValueError("REDDIT_CLIENT_SECRET is required")

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _truncate_text(value: Any, max_len: int = 4000) -> str:
    text = "" if value is None else str(value)
    return text[:max_len]


def _post_to_dict(post: Any, include_body: bool = True) -> dict[str, Any]:
    return {
        "id": post.id,
        "name": _safe_attr(post, "name"),
        "title": _truncate_text(_safe_attr(post, "title"), 1000),
        "author": str(post.author) if _safe_attr(post, "author") else None,
        "subreddit": str(post.subreddit) if _safe_attr(post, "subreddit") else None,
        "score": _safe_attr(post, "score"),
        "upvote_ratio": _safe_attr(post, "upvote_ratio"),
        "num_comments": _safe_attr(post, "num_comments"),
        "created_utc": _safe_attr(post, "created_utc"),
        "url": _safe_attr(post, "url"),
        "permalink": f"https://www.reddit.com{post.permalink}" if _safe_attr(post, "permalink") else None,
        "is_self": _safe_attr(post, "is_self"),
        "over_18": _safe_attr(post, "over_18"),
        "spoiler": _safe_attr(post, "spoiler"),
        "stickied": _safe_attr(post, "stickied"),
        "locked": _safe_attr(post, "locked"),
        "link_flair_text": _safe_attr(post, "link_flair_text"),
        "selftext": _truncate_text(_safe_attr(post, "selftext"), 4000) if include_body else "",
    }


def _comment_to_dict(comment: Any) -> dict[str, Any]:
    return {
        "id": comment.id,
        "author": str(comment.author) if _safe_attr(comment, "author") else None,
        "score": _safe_attr(comment, "score"),
        "created_utc": _safe_attr(comment, "created_utc"),
        "body": _truncate_text(_safe_attr(comment, "body"), 4000),
        "permalink": f"https://www.reddit.com{comment.permalink}" if _safe_attr(comment, "permalink") else None,
        "is_submitter": _safe_attr(comment, "is_submitter"),
        "stickied": _safe_attr(comment, "stickied"),
        "depth": _safe_attr(comment, "depth"),
    }


def _submission_with_top_comments(submission: Any, *, comment_sort: str, comment_limit: int) -> dict[str, Any]:
    submission.comment_sort = comment_sort
    submission.comments.replace_more(limit=0)
    comments = [_comment_to_dict(comment) for comment in submission.comments[:comment_limit]]
    return {
        "post": _post_to_dict(submission, include_body=True),
        "comment_sort": comment_sort,
        "comments_count_returned": len(comments),
        "comments": comments,
    }


def _load_submission_from_url(url: str, *, comment_sort: str, comment_limit: int) -> dict[str, Any]:
    reddit = _build_reddit_client()
    submission = reddit.submission(url=url)
    _ = submission.id
    return _submission_with_top_comments(submission, comment_sort=comment_sort, comment_limit=comment_limit)


def _safe_enrich_post(permalink: str, *, comment_sort: str, comment_limit: int) -> tuple[
    str, dict[str, Any] | None, str | None]:
    try:
        bundle = _load_submission_from_url(
            permalink,
            comment_sort=comment_sort,
            comment_limit=comment_limit,
        )
        return permalink, bundle, None
    except Exception as exc:
        return permalink, None, str(exc)


def _enrich_posts_if_sparse(
        posts: list[dict[str, Any]],
        *,
        sparse_threshold: int,
        comment_limit: int,
        comment_sort: str,
) -> tuple[list[dict[str, Any]], int]:
    if len(posts) >= sparse_threshold:
        return posts, 0

    to_enrich = [post.get("permalink") for post in posts if post.get("permalink")]
    if not to_enrich:
        return posts, 0

    enriched_lookup: dict[str, dict[str, Any]] = {}
    workers = min(MAX_WORKERS, len(to_enrich))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _safe_enrich_post,
                permalink,
                comment_sort=comment_sort,
                comment_limit=comment_limit,
            ): permalink
            for permalink in to_enrich
        }
        for future in as_completed(futures):
            permalink, bundle, error = future.result()
            if bundle is None:
                continue
            enriched_lookup[permalink] = {
                "post_details": bundle["post"],
                "top_comments": bundle["comments"],
                "comments_count_returned": bundle["comments_count_returned"],
                "comment_sort": bundle["comment_sort"],
                "enriched": True,
            }

    merged: list[dict[str, Any]] = []
    enriched_count = 0
    for post in posts:
        permalink = post.get("permalink")
        if permalink in enriched_lookup:
            merged_post = {**post, **enriched_lookup[permalink]}
            merged.append(merged_post)
            enriched_count += 1
        else:
            merged.append(post)

    return merged, enriched_count


def _get_subreddit(reddit: praw.Reddit, subreddit_name: str):
    try:
        subreddit = reddit.subreddit(subreddit_name)
        _ = subreddit.display_name
        return subreddit
    except (NotFound, Redirect):
        raise ValueError(f"Subreddit not found: {subreddit_name}")
    except Forbidden as exc:
        raise RuntimeError(f"Access forbidden for subreddit: {subreddit_name}") from exc


def _format_multi_subreddit_output(
        *,
        mode: str,
        subreddit_payloads: list[dict[str, Any]],
        requested_subreddits: list[str],
        single_subreddit_label: str | None = None,
        query: str | None = None,
        time_filter: str | None = None,
) -> dict[str, Any]:
    total_posts = sum(item["count"] for item in subreddit_payloads)
    output: dict[str, Any] = {
        "mode": mode,
        "requested_subreddits": requested_subreddits,
        "requested_subreddit_count": len(requested_subreddits),
        "total_posts": total_posts,
        "subreddits": subreddit_payloads,
    }
    if query is not None:
        output["query"] = query
    if time_filter is not None:
        output["time_filter"] = time_filter

    if single_subreddit_label is not None and len(subreddit_payloads) == 1:
        only = subreddit_payloads[0]
        output["subreddit"] = single_subreddit_label
        output["count"] = only["count"]
        output["posts"] = only["posts"]
        output["enriched_count"] = only.get("enriched_count", 0)

    return output


def latest_posts(
        subreddit: str | list[str],
        limit: int = 10,
        sparse_enrich_threshold: int = DEFAULT_SPARSE_ENRICH_THRESHOLD,
        sparse_comment_limit: int = DEFAULT_SPARSE_COMMENT_LIMIT,
        sparse_comment_sort: str = "top",
) -> dict[str, Any]:
    subreddits = _normalize_subreddits(subreddit)
    limit = _require_int(limit, "limit", min_value=1, max_value=MAX_LISTING_LIMIT)
    sparse_enrich_threshold = _require_int(sparse_enrich_threshold, "sparse_enrich_threshold", min_value=1,
                                           max_value=30)
    sparse_comment_limit = _require_int(sparse_comment_limit, "sparse_comment_limit", min_value=1, max_value=25)
    sparse_comment_sort = _validate_choice(sparse_comment_sort, "sparse_comment_sort", VALID_COMMENT_SORTS, "top")

    reddit = _build_reddit_client()
    payloads: list[dict[str, Any]] = []

    for name in subreddits:
        sub = _get_subreddit(reddit, name)
        posts = [_post_to_dict(post, include_body=False) for post in sub.new(limit=limit)]
        posts, enriched_count = _enrich_posts_if_sparse(
            posts,
            sparse_threshold=sparse_enrich_threshold,
            comment_limit=sparse_comment_limit,
            comment_sort=sparse_comment_sort,
        )
        payloads.append(
            {
                "subreddit": name,
                "count": len(posts),
                "enriched_count": enriched_count,
                "posts": posts,
            }
        )

    return _format_multi_subreddit_output(
        mode="new",
        subreddit_payloads=payloads,
        requested_subreddits=subreddits,
        single_subreddit_label=subreddits[0] if len(subreddits) == 1 else None,
    )


def top_posts(
        subreddit: str | list[str],
        limit: int = 10,
        time_filter: str = "week",
        sparse_enrich_threshold: int = DEFAULT_SPARSE_ENRICH_THRESHOLD,
        sparse_comment_limit: int = DEFAULT_SPARSE_COMMENT_LIMIT,
        sparse_comment_sort: str = "top",
) -> dict[str, Any]:
    subreddits = _normalize_subreddits(subreddit)
    limit = _require_int(limit, "limit", min_value=1, max_value=MAX_LISTING_LIMIT)
    time_filter = _validate_choice(time_filter, "time_filter", VALID_TOP_TIME_FILTERS, "week")
    sparse_enrich_threshold = _require_int(sparse_enrich_threshold, "sparse_enrich_threshold", min_value=1,
                                           max_value=30)
    sparse_comment_limit = _require_int(sparse_comment_limit, "sparse_comment_limit", min_value=1, max_value=25)
    sparse_comment_sort = _validate_choice(sparse_comment_sort, "sparse_comment_sort", VALID_COMMENT_SORTS, "top")

    reddit = _build_reddit_client()
    payloads: list[dict[str, Any]] = []

    for name in subreddits:
        sub = _get_subreddit(reddit, name)
        posts = [_post_to_dict(post, include_body=False) for post in sub.top(time_filter=time_filter, limit=limit)]
        posts, enriched_count = _enrich_posts_if_sparse(
            posts,
            sparse_threshold=sparse_enrich_threshold,
            comment_limit=sparse_comment_limit,
            comment_sort=sparse_comment_sort,
        )
        payloads.append(
            {
                "subreddit": name,
                "count": len(posts),
                "enriched_count": enriched_count,
                "posts": posts,
            }
        )

    result = _format_multi_subreddit_output(
        mode="top",
        subreddit_payloads=payloads,
        requested_subreddits=subreddits,
        single_subreddit_label=subreddits[0] if len(subreddits) == 1 else None,
        time_filter=time_filter,
    )
    result["time_filter"] = time_filter
    return result


def search_subreddit(
        subreddit: str | list[str],
        query: str,
        limit: int = 10,
        sort: str = "new",
        time_filter: str = "week",
) -> dict[str, Any]:
    subreddits = _normalize_subreddits(subreddit)
    query = _require_text(query, "query", max_len=500)
    limit = _require_int(limit, "limit", min_value=1, max_value=MAX_LISTING_LIMIT)
    sort = _validate_choice(sort, "sort", VALID_SEARCH_SORTS, "new")
    time_filter = _validate_choice(time_filter, "time_filter", VALID_SEARCH_TIME_FILTERS, "week")

    reddit = _build_reddit_client()
    payloads: list[dict[str, Any]] = []

    for name in subreddits:
        sub = _get_subreddit(reddit, name)
        posts = [
            _post_to_dict(post, include_body=False)
            for post in sub.search(query=query, sort=sort, time_filter=time_filter, limit=limit)
        ]
        payloads.append(
            {
                "subreddit": name,
                "count": len(posts),
                "posts": posts,
            }
        )

    result = _format_multi_subreddit_output(
        mode="search_subreddit",
        subreddit_payloads=payloads,
        requested_subreddits=subreddits,
        single_subreddit_label=subreddits[0] if len(subreddits) == 1 else None,
        query=query,
        time_filter=time_filter,
    )
    result["sort"] = sort
    return result


def search_all(
        query: str,
        limit: int = 10,
        sort: str = "new",
        time_filter: str = "week",
) -> dict[str, Any]:
    query = _require_text(query, "query", max_len=500)
    limit = _require_int(limit, "limit", min_value=1, max_value=MAX_LISTING_LIMIT)
    sort = _validate_choice(sort, "sort", VALID_SEARCH_SORTS, "new")
    time_filter = _validate_choice(time_filter, "time_filter", VALID_SEARCH_TIME_FILTERS, "week")

    reddit = _build_reddit_client()
    sub = reddit.subreddit("all")
    posts = [
        _post_to_dict(post, include_body=False)
        for post in sub.search(query=query, sort=sort, time_filter=time_filter, limit=limit)
    ]

    return {
        "mode": "search_all",
        "query": query,
        "sort": sort,
        "time_filter": time_filter,
        "count": len(posts),
        "posts": posts,
    }


def get_post(
        url: str,
        comment_limit: int = 20,
        comment_sort: str = "top",
) -> dict[str, Any]:
    url = _require_text(url, "url", max_len=2000)
    comment_limit = _require_int(comment_limit, "comment_limit", min_value=1, max_value=100)
    comment_sort = _validate_choice(comment_sort, "comment_sort", VALID_COMMENT_SORTS, "top")

    if "/comments/" not in url:
        raise ValueError(
            "url must be a Reddit post permalink containing '/comments/'. "
            "If you only have a subreddit name, use latest_posts or top_posts."
        )

    try:
        bundle = _load_submission_from_url(url, comment_sort=comment_sort, comment_limit=comment_limit)
    except Exception as exc:
        raise ValueError(
            f"Could not load post from url: {url}. "
            "Ask for a valid Reddit post permalink or use latest_posts/top_posts first."
        ) from exc

    return bundle


def subreddit_info(subreddit: str | list[str]) -> dict[str, Any]:
    subreddits = _normalize_subreddits(subreddit)
    reddit = _build_reddit_client()

    items: list[dict[str, Any]] = []
    for name in subreddits:
        sub = _get_subreddit(reddit, name)
        items.append(
            {
                "display_name": _safe_attr(sub, "display_name"),
                "title": _safe_attr(sub, "title"),
                "public_description": _truncate_text(_safe_attr(sub, "public_description"), 2000),
                "description": _truncate_text(_safe_attr(sub, "description"), 4000),
                "subscribers": _safe_attr(sub, "subscribers"),
                "active_user_count": _safe_attr(sub, "active_user_count"),
                "over18": _safe_attr(sub, "over18"),
                "url": f"https://www.reddit.com{sub.url}" if _safe_attr(sub, "url") else None,
            }
        )

    if len(items) == 1:
        return items[0]

    return {
        "count": len(items),
        "subreddits": items,
    }
