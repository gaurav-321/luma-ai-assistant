import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.work.researcher.researcher_tools import search_and_summarize


def _ok(operation: str | None, result):
    return {
        "ok": True,
        "operation": operation,
        "result": result,
        "error": None,
    }


def _fail(operation: str | None, error: str, recovery_hint: str):
    return {
        "ok": False,
        "operation": operation,
        "result": None,
        "error": error,
    }


async def process_args(args: dict | str | None = None, extra_args: dict = None) -> dict:
    del extra_args
    args = args or {}
    operation = "search_and_summarize"
    try:
        query = args.get("query")
        if not query:
            return _fail(operation, "query is required", "Retry with args.query describing what to research.")

        mode = str(args.get("mode", "slow")).strip().lower()
        if mode not in {"slow", "fast"}:
            return _fail(
                operation,
                "mode must be either 'slow' or 'fast'",
                "Retry with args.mode set to 'slow' (default) or 'fast'.",
            )

        timeout_seconds = 30 if mode == "fast" else 500

        # search_and_summarize is blocking I/O + LLM work; run in thread.
        result = await asyncio.wait_for(
            asyncio.to_thread(search_and_summarize, query, mode),
            timeout=timeout_seconds,
        )
        return _ok(operation, result)

    except asyncio.TimeoutError:
        return _fail(
            operation,
            "Research request timed out.",
            "Retry with args.mode='fast' for metadata-only output or use a narrower query.",
        )
    except Exception as exc:
        return _fail(
            operation,
            str(exc),
            "Retry with a narrower query (topic + timeframe) or check external search service availability.",
        )


def main() -> None:
    import asyncio

    cases = [
        {"name": "missing_query", "args": {}},
        {"name": "search_and_summarize", "args": {"query": "what is openclaw"}},
    ]

    outputs = []
    for case in cases:
        result = asyncio.run(process_args(case["args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
