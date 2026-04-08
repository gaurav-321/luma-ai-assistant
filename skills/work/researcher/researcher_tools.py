import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Optional, Tuple, Set

import requests
import trafilatura

from core.utils.config import client, MODEL, scraper

max_workers: int = 8
top_k_results: int = 8
SEARXNG = os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8081")
fetch_pages: bool = True
time_range = "year"
FAST_MODE_SEARCH_TIMEOUT_SECONDS = 6
FAST_MODE_URL_TIMEOUT_SECONDS = 2.0
FAST_MODE_TOTAL_LOAD_WINDOW_SECONDS = 5.0

# Context / prompt limits
MAX_TOTAL_PROMPT_CHARS = 120000
MAX_ARTICLE_CHARS = 16000
MAX_RELEVANT_PASSAGES_PER_ARTICLE = 8
PASSAGE_WINDOW_CHARS = 1200
MIN_PASSAGE_CHARS = 200

system_prompt_news_multi = """
You are a precise research and news summarization assistant.

You will receive:
- a user query
- several search results
- extracted article text from multiple pages
- optionally relevance-focused passages from each article

Your job is to produce one detailed combined answer that is tightly relevant to the user's query.

Strict rules:
1. Use only the provided material.
2. Do not invent facts.
3. Prefer information directly relevant to the query.
4. Ignore generic filler, ads, navigation text, and irrelevant article sections.
5. If sources disagree, explicitly describe the disagreement.
6. If the query asks for latest developments, emphasize the most recent relevant developments mentioned in the provided material.
7. Clearly separate confirmed facts, source differences, and uncertainty.
8. Do not ramble.
9. Keep the output detailed and useful.

Output format in markdown:

## Direct Answer
Give a direct answer to the user's question in 2-5 paragraphs.

## Key Developments
Bullet points with the most relevant facts and developments.

## Source-by-Source Breakdown
For each useful source:
- Source number and title
- 3-6 bullets describing what that source specifically contributes
- mention if only partially relevant

## Agreements and Differences
Bullet points describing where sources align or differ.

## Gaps / Uncertainty
Mention what is missing, unclear, speculative, or unsupported by the provided material.

## Final Takeaway
A concise synthesis of what is most likely true overall based on the provided material.
"""

user_prompt_news_multi = """
Summarize the following search results in one combined response.

Focus tightly on the user's query and extract only what is relevant.
Use the relevance passages first, but consult the full extracted text when needed.

Input JSON:
{payload}
"""


# -------------------------
# SearXNG search
# -------------------------
def searxng_search(
        base_url: str,
        query: str,
        *,
        page: int = 1,
        lang: str = "en",
        categories: Optional[str] = None,
        engines: Optional[str] = None,
        time_range_int: Optional[str] = None,
        timeout: int = 30,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/search"
    params = {"q": query, "format": "json", "pageno": page, "lang": lang}
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if time_range_int:
        params["time_range"] = time_range_int

    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# -------------------------
# Page extraction
# -------------------------
def extract_html_parsed(url: str, *, timeout: int = 10) -> str:
    """
    Fetch URL HTML and return Trafilatura extracted main content text.
    """
    r = scraper.get(url, timeout=timeout)
    r.raise_for_status()

    extracted = trafilatura.extract(
        r.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    return (extracted or "").strip()


# -------------------------
# Query-aware relevance extraction
# -------------------------
STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "of", "in", "on", "at", "to",
    "for", "from", "with", "by", "about", "into", "over", "after", "before", "during", "latest",
    "news", "what", "when", "where", "who", "why", "how", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "their", "them", "this", "that", "these", "those", "as",
    "up", "down", "out", "off", "through", "can", "could", "would", "should", "will", "may",
    "might", "do", "does", "did", "have", "has", "had"
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def split_into_paragraphs(text: str) -> List[str]:
    if not text:
        return []
    paras = re.split(r"\n{2,}|\r\n\r\n+", text)
    cleaned = [normalize_space(p) for p in paras]
    return [p for p in cleaned if len(p) >= 40]


def tokenize_query(query: str) -> List[str]:
    raw = re.findall(r"[a-zA-Z0-9']+", query.lower())
    tokens = [t for t in raw if len(t) > 2 and t not in STOPWORDS]
    return tokens


def generate_query_phrases(query: str) -> List[str]:
    q = normalize_space(query.lower())
    phrases = []

    # full query
    if q:
        phrases.append(q)

    # quoted phrases
    phrases.extend(re.findall(r'"([^"]+)"', q))

    return list(dict.fromkeys([p.strip() for p in phrases if p.strip()]))


def score_paragraph(para: str, query_tokens: List[str], query_phrases: List[str]) -> float:
    text = para.lower()
    score = 0.0

    # Exact phrase bonus
    for phrase in query_phrases:
        if phrase and phrase in text:
            score += 8.0

    # Token overlap
    token_hits = 0
    for tok in query_tokens:
        if tok in text:
            token_hits += 1
            score += 1.5

    # Density bonus
    para_len = max(len(text), 1)
    density = token_hits / max(len(query_tokens), 1)
    score += density * 4.0

    # Mild date / recency cues
    if re.search(r"\b(20\d{2}|19\d{2})\b", text):
        score += 0.5
    if re.search(r"\b(today|yesterday|this week|this month|recent|recently|latest|update|updated)\b", text):
        score += 0.75

    # Penalize likely junk
    if len(text) < 80:
        score -= 1.5
    if text.count("|") > 5:
        score -= 2.0

    return score


def extract_relevant_passages(
        query: str,
        title: str,
        excerpt: str,
        text: str,
        *,
        max_passages: int = MAX_RELEVANT_PASSAGES_PER_ARTICLE,
        max_chars: int = MAX_ARTICLE_CHARS,
) -> str:
    """
    Extract the most query-relevant passages from an article.
    Falls back to a prefix if relevance scoring finds too little.
    """
    if not text.strip():
        return ""

    text = normalize_space(text)
    text = text[:max_chars]

    query_tokens = tokenize_query(query)
    query_phrases = generate_query_phrases(query)

    # Boost with title / excerpt terms too
    context_tokens = tokenize_query(f"{query} {title} {excerpt}")
    context_phrases = list(dict.fromkeys(query_phrases + generate_query_phrases(title)))

    paragraphs = split_into_paragraphs(text)
    if not paragraphs:
        return text[: min(len(text), 3000)]

    scored: List[Tuple[float, int, str]] = []
    for idx, para in enumerate(paragraphs):
        score = score_paragraph(para, context_tokens, context_phrases)
        if score > 0:
            scored.append((score, idx, para))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected: List[str] = []
    seen = set()
    total_chars = 0

    for score, idx, para in scored[: max_passages * 2]:
        p = para.strip()
        if not p or p in seen:
            continue
        if len(p) < MIN_PASSAGE_CHARS:
            continue

        if total_chars + len(p) > 5000 and selected:
            break

        selected.append(p)
        seen.add(p)
        total_chars += len(p)

        if len(selected) >= max_passages:
            break

    # Fallback if relevance scoring found little
    if not selected:
        fallback = text[: min(len(text), 3500)]
        return fallback.strip()

    return "\n\n".join(selected).strip()


# -------------------------
# Parallel fetch + extract
# -------------------------
def _safe_extract_page(
        query: str,
        url: str,
        title: str,
        excerpt: str,
) -> Tuple[str, str, Optional[str]]:
    """
    Return (parsed_text, relevant_passages, error)
    Never raises.
    """
    try:
        parsed = extract_html_parsed(url)
        if not parsed:
            return "", "", "Empty extraction"

        relevant = extract_relevant_passages(
            query=query,
            title=title,
            excerpt=excerpt,
            text=parsed,
        )
        return parsed, relevant, None
    except Exception as e:
        return "", "", f"{type(e).__name__}: {e}"


def _extract_search_metadata(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = result.get("url")
    if not url:
        return None

    engines = result.get("engines")
    if isinstance(engines, str):
        engines = [engines]
    if not isinstance(engines, list):
        engines = []

    category = result.get("category")
    if isinstance(category, list):
        category = ", ".join([str(c) for c in category if c])

    return {
        "title": normalize_space(result.get("title") or "") or "Untitled",
        "url": url,
        "excerpt": normalize_space(result.get("content") or result.get("snippet") or ""),
        "engines": engines,
        "category": category or "",
        "published_date": result.get("publishedDate") or result.get("published_date") or "",
        "score": result.get("score"),
    }


def _probe_url_load(url: str, *, timeout: float) -> Dict[str, Any]:
    start = time.perf_counter()
    out: Dict[str, Any] = {
        "requested_url": url,
        "final_url": url,
        "status_code": None,
        "elapsed_ms": 0,
        "loaded": False,
        "error": None,
    }

    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        out["final_url"] = response.url or url
        out["status_code"] = response.status_code
        out["loaded"] = response.ok
        if not response.ok:
            out["error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out["elapsed_ms"] = int((time.perf_counter() - start) * 1000)

    return out


def _load_urls_within_window(metadata_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata_items:
        return {"loaded": [], "failed": [], "timed_out": []}

    max_workers_fast = min(max_workers, len(metadata_items))
    max_workers_fast = max(max_workers_fast, 1)
    future_to_url: Dict[Any, str] = {}
    loaded: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers_fast) as ex:
        for item in metadata_items:
            url = item.get("url")
            if not url:
                continue
            future = ex.submit(_probe_url_load, url, timeout=FAST_MODE_URL_TIMEOUT_SECONDS)
            future_to_url[future] = url

        try:
            for fut in as_completed(
                    future_to_url,
                    timeout=FAST_MODE_TOTAL_LOAD_WINDOW_SECONDS,
            ):
                probe = fut.result()
                if probe.get("loaded"):
                    loaded.append(probe)
                else:
                    failed.append(probe)
        except FuturesTimeoutError:
            pass

        timed_out: List[str] = []
        for fut, url in future_to_url.items():
            if not fut.done():
                fut.cancel()
                timed_out.append(url)

    return {
        "loaded": loaded,
        "failed": failed,
        "timed_out": timed_out,
    }


# -------------------------
# Single LLM call for all articles
# -------------------------
def summarize_all_with_llm(query: str, items: List[Dict[str, Any]]) -> str:
    usable_articles: List[Dict[str, Any]] = []
    total_chars = 0

    for idx, item in enumerate(items, start=1):
        parsed = (item.get("html_parsed") or "").strip()
        relevant = (item.get("relevant_passages") or "").strip()

        if not parsed:
            continue

        article = {
            "source_number": idx,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "excerpt": item.get("excerpt", ""),
            "relevant_passages": relevant[:5000],
            "full_text": parsed[:MAX_ARTICLE_CHARS],
        }

        serialized = json.dumps(article, ensure_ascii=False)
        if total_chars + len(serialized) > MAX_TOTAL_PROMPT_CHARS and usable_articles:
            break

        usable_articles.append(article)
        total_chars += len(serialized)

    if not usable_articles:
        return "No extracted article content was available to summarize."

    payload = json.dumps(
        {
            "query": query,
            "article_count": len(usable_articles),
            "articles": usable_articles,
        },
        ensure_ascii=False,
    )

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt_news_multi},
            {"role": "user", "content": user_prompt_news_multi.format(payload=payload)},
        ],
    )

    return (response.choices[0].message.content or "").strip()


# -------------------------
# Search -> fetch -> summarize
# -------------------------
def search_and_summarize(query: str, mode: str = "slow") -> str:
    normalized_mode = (mode or "slow").strip().lower()
    if normalized_mode not in {"slow", "fast"}:
        raise ValueError(f"Unsupported mode: {mode}")

    data = searxng_search(
        SEARXNG,
        query=query,
        page=1,
        time_range_int=time_range,
        timeout=FAST_MODE_SEARCH_TIMEOUT_SECONDS if normalized_mode == "fast" else 30,
    )

    results = data.get("results", []) or []
    metadata_items: List[Dict[str, Any]] = []
    for r in results:
        item = _extract_search_metadata(r)
        if item:
            metadata_items.append(item)

    if normalized_mode == "fast":
        load_report = _load_urls_within_window(metadata_items)
        fast_output = {
            "query": query,
            "mode": "fast",
            "fetched_links": metadata_items,
            "loaded_urls": load_report.get("loaded", []),
            "failed_urls": load_report.get("failed", []),
            "timed_out_urls": load_report.get("timed_out", []),
            "result_count": len(metadata_items),
        }
        return fast_results_to_markdown(fast_output)

    items: List[Dict[str, Any]] = []
    for r in metadata_items[:top_k_results]:
        items.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "excerpt": r.get("excerpt", ""),
            "html_parsed": "",
            "relevant_passages": "",
            "error": None,
        })

    if not fetch_pages or not items:
        output = {
            "query": query,
            "top_results": items,
            "overall_summary": "",
        }
        return results_to_markdown(output)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_idx = {
            ex.submit(
                _safe_extract_page,
                query,
                item["url"],
                item["title"],
                item["excerpt"],
            ): i
            for i, item in enumerate(items)
        }

        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            parsed, relevant, err = fut.result()
            items[i]["html_parsed"] = parsed
            items[i]["relevant_passages"] = relevant
            items[i]["error"] = err

    combined_summary = sanitize_llm_input(summarize_all_with_llm(query=query, items=items))

    output = {
        "query": query,
        "top_results": items,
        "overall_summary": combined_summary,
    }
    return results_to_markdown(output)


CONTROL_PATTERNS = [
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|endoftext\|>",
    r"(?im)^\s*(system|user|assistant)\s*:",
    r"(?im)^full_text\s*:",
]


def sanitize_llm_input(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    for pat in CONTROL_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


# -------------------------
# Markdown output
# -------------------------
def results_to_markdown(result: Dict[str, Any]) -> str:
    md: List[str] = []
    md.append("# Search Results")
    md.append(f"**Query:** {result.get('query', '')}\n")

    overall_summary = (result.get("overall_summary") or "").strip()
    if overall_summary:
        md.append("## Combined Summary")
        md.append(overall_summary)
        md.append("")

    md.append("## Sources")

    for i, item in enumerate(result.get("top_results", []), 1):
        title = item.get("title", "Untitled")
        url = item.get("url", "")
        excerpt = item.get("excerpt", "")
        error = item.get("error")
        relevant = (item.get("relevant_passages") or "").strip()
        if error:
            continue
        md.append(f"### {i}. [{title}]({url})")
    return "\n".join(md)


def fast_results_to_markdown(result: Dict[str, Any]) -> str:
    md: List[str] = []
    md.append("# Search Metadata")
    md.append(f"**Query:** {result.get('query', '')}")
    md.append("**Mode:** fast")
    md.append(f"**Fetched Links:** {len(result.get('fetched_links', []))}")
    md.append("")

    md.append("## Fetched Links")
    fetched_links = result.get("fetched_links", [])
    if not fetched_links:
        md.append("- No links were returned by SearXNG.")
    else:
        for i, item in enumerate(fetched_links, 1):
            engines = ", ".join(item.get("engines", [])) or "unknown"
            category = item.get("category") or "unknown"
            published_date = item.get("published_date") or "n/a"
            score = item.get("score")
            score_text = "n/a" if score is None else str(score)

            md.append(f"### {i}. [{item.get('title', 'Untitled')}]({item.get('url', '')})")
            md.append(f"- engines: {engines}")
            md.append(f"- category: {category}")
            md.append(f"- published: {published_date}")
            md.append(f"- score: {score_text}")
            excerpt = item.get("excerpt", "")
            if excerpt:
                md.append(f"- excerpt: {excerpt}")

    md.append("")
    md.append("## URLs Loaded Within 5 Seconds")
    loaded = result.get("loaded_urls", [])
    if not loaded:
        md.append("- No URLs completed successfully in the 5-second load window.")
    else:
        for i, probe in enumerate(loaded, 1):
            md.append(
                f"{i}. `{probe.get('requested_url')}` -> `{probe.get('final_url')}` "
                f"(status: {probe.get('status_code')}, time: {probe.get('elapsed_ms')}ms)"
            )

    failed = result.get("failed_urls", [])
    timed_out = result.get("timed_out_urls", [])
    if failed or timed_out:
        md.append("")
        md.append("## Not Loaded")
        for probe in failed:
            md.append(
                f"- `{probe.get('requested_url')}` "
                f"(status: {probe.get('status_code')}, error: {probe.get('error')}, time: {probe.get('elapsed_ms')}ms)"
            )
        for url in timed_out:
            md.append(f"- `{url}` (timed out in global 5-second window)")

    return "\n".join(md)


if __name__ == "__main__":
    out = search_and_summarize(query="epstein latest news")
    print(out)
