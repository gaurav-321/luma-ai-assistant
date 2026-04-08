import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from core.utils.config import DEBUG


def indent_text(text: str, prefix: str = "    ") -> str:
    if not text:
        return prefix + "<empty>"
    return "\n".join(prefix + line for line in str(text).splitlines())


def debug_profile(
        mem_search: float,
        llm_time: float,
        mem_write: float,
        total_time: float,
        usage=None
):
    """Pretty debug + token/sec stats."""
    if not DEBUG:
        return

    print("\n========== DEBUG TIMINGS ==========")
    print(f"Memory search   : {mem_search:.3f}s")
    print(f"LLM generation  : {llm_time:.3f}s")
    print(f"Memory write    : {mem_write:.3f}s")
    print(f"TOTAL time      : {total_time:.3f}s")

    if usage:
        prompt = getattr(usage, "prompt_tokens", 0)
        completion = getattr(usage, "completion_tokens", 0)
        total = getattr(usage, "total_tokens", 0)
        tps = completion / llm_time if llm_time > 0 else 0

        print("\n---------- TOKEN STATS ----------")
        print(f"Prompt tokens     : {prompt}")
        print(f"Completion tokens : {completion}")
        print(f"Total tokens      : {total}")
        print(f"Throughput        : {tps:.2f} tokens/sec")

    print("=================================\n")


def debug_log(*args, **kwargs):
    """Print only if DEBUG enabled."""
    if DEBUG:
        print(*args, **kwargs)


@dataclass
class StreamDebugStats:
    agent_name: str
    task: str

    total_time: float = 0.0
    first_token_latency: Optional[float] = None

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    llm_calls: int = 0
    tool_requests: int = 0
    tool_executions: int = 0
    thought_events: int = 0
    handoffs: int = 0
    error_count: int = 0

    tokens_per_second_completion: float = 0.0
    tokens_per_second_total: float = 0.0

    last_event_type: Optional[str] = None
    last_message: Optional[str] = None
    last_message_source: Optional[str] = None
    final_target: Optional[str] = None
    stop_reason: Optional[str] = None

    raw_last_event: Any = None
    event_types: list[str] = field(default_factory=list)


def debug_profile(
        mem_search: float,
        llm_time: float,
        mem_write: float,
        total_time: float,
        usage=None,
):
    """Pretty debug + token/sec stats."""
    if not DEBUG:
        return

    print("\n========== DEBUG TIMINGS ==========")
    print(f"Memory search   : {mem_search:.3f}s")
    print(f"LLM generation  : {llm_time:.3f}s")
    print(f"Memory write    : {mem_write:.3f}s")
    print(f"TOTAL time      : {total_time:.3f}s")

    if usage:
        prompt = getattr(usage, "prompt_tokens", 0)
        completion = getattr(usage, "completion_tokens", 0)
        total = getattr(usage, "total_tokens", 0)
        tps = completion / llm_time if llm_time > 0 else 0

        print("\n---------- TOKEN STATS ----------")
        print(f"Prompt tokens     : {prompt}")
        print(f"Completion tokens : {completion}")
        print(f"Total tokens      : {total}")
        print(f"Throughput        : {tps:.2f} completion tok/sec")

    print("=================================\n")


def debug_log(*args, **kwargs):
    """Print only if DEBUG enabled."""
    if DEBUG:
        print(*args, **kwargs)


def _safe_get_usage_tokens(usage):
    if not usage:
        return 0, 0, 0

    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0

    if not total:
        total = prompt + completion

    return prompt, completion, total


def _extract_event_content(event) -> Optional[str]:
    content = getattr(event, "content", None)

    if content is None:
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        try:
            return "\n".join(str(x) for x in content).strip()
        except Exception:
            return str(content)

    return str(content).strip()


async def debug_run_stream(agent, task, return_dict: bool = True):
    """
    Debug wrapper for agent.run_stream()

    Features:
    - prints every event
    - returns structured stream stats
    - computes tokens/sec
    - captures last meaningful message
    """

    stats = StreamDebugStats(agent_name=agent.name, task=task)

    start_time = time.perf_counter()
    first_usage_seen = False

    if DEBUG:
        print("\n========== AGENT STREAM DEBUG ==========")
        print("Agent:", agent.name)
        print("Task :", task)
        print("========================================\n")

    try:
        stream = agent.run_stream(task=task)

        async for event in stream:
            event_type = type(event).__name__
            stats.last_event_type = event_type
            stats.raw_last_event = event
            stats.event_types.append(event_type)

            if DEBUG:
                print(f"\n--- EVENT [{event_type}] ---")
                print(event)

            if hasattr(event, "source") and DEBUG:
                print("SOURCE:", event.source)

            if hasattr(event, "content") and DEBUG:
                print("CONTENT:", event.content)

            if hasattr(event, "models_usage") and DEBUG:
                print("TOKENS:", event.models_usage)

            if hasattr(event, "target") and DEBUG:
                print("TARGET:", event.target)

            usage = getattr(event, "models_usage", None)
            if usage:
                prompt, completion, total = _safe_get_usage_tokens(usage)

                stats.total_prompt_tokens += prompt
                stats.total_completion_tokens += completion
                stats.total_tokens += total
                stats.llm_calls += 1

                if not first_usage_seen:
                    stats.first_token_latency = time.perf_counter() - start_time
                    first_usage_seen = True

            if event_type == "ToolCallRequestEvent":
                stats.tool_requests += 1
            elif event_type == "ToolCallExecutionEvent":
                stats.tool_executions += 1
            elif event_type == "ThoughtEvent":
                stats.thought_events += 1
            elif event_type == "HandoffMessage":
                stats.handoffs += 1
                stats.final_target = getattr(event, "target", None)

            content_text = _extract_event_content(event)
            source = getattr(event, "source", None)

            # Capture last meaningful natural-language-like message
            if content_text and event_type in {
                "TextMessage",
                "ThoughtEvent",
                "HandoffMessage",
                "ToolCallExecutionEvent",
                "TaskResult",
            }:
                stats.last_message = content_text
                stats.last_message_source = source

            # Handle TaskResult specially
            if event_type == "TaskResult":
                stats.stop_reason = getattr(event, "stop_reason", None)

                messages = getattr(event, "messages", None)
                if messages:
                    # Walk backward to find last message with content
                    for msg in reversed(messages):
                        msg_content = getattr(msg, "content", None)
                        if msg_content:
                            if isinstance(msg_content, str):
                                stats.last_message = msg_content.strip()
                            else:
                                stats.last_message = str(msg_content)
                            stats.last_message_source = getattr(msg, "source", None)
                            break


    except Exception as e:

        stats.error_count += 1

        stats.last_message = str(e)

        stats.last_message_source = "exception"

        stats.error_type = type(e).__name__

        stats.traceback = traceback.format_exc()

        if DEBUG:
            print("\nSTREAM ERROR")

            print(f"TYPE: {stats.error_type}")

            print(f"MESSAGE: {e}")

            print(stats.traceback)

    finally:
        stats.total_time = time.perf_counter() - start_time

        if stats.total_time > 0:
            stats.tokens_per_second_completion = (
                    stats.total_completion_tokens / stats.total_time
            )
            stats.tokens_per_second_total = stats.total_tokens / stats.total_time

        if DEBUG:
            print("\n========== STREAM SUMMARY ==========")
            print(f"Total time              : {stats.total_time:.3f}s")
            print(f"First token latency     : {stats.first_token_latency}")
            print(f"LLM calls               : {stats.llm_calls}")
            print(f"Tool requests           : {stats.tool_requests}")
            print(f"Tool executions         : {stats.tool_executions}")
            print(f"Thought events          : {stats.thought_events}")
            print(f"Handoffs                : {stats.handoffs}")
            print(f"Errors                  : {stats.error_count}")
            print(f"Prompt tokens           : {stats.total_prompt_tokens}")
            print(f"Completion tokens       : {stats.total_completion_tokens}")
            print(f"Total tokens            : {stats.total_tokens}")
            print(
                f"Completion tok/sec      : {stats.tokens_per_second_completion:.2f}"
            )
            print(f"Total tok/sec           : {stats.tokens_per_second_total:.2f}")
            print(f"Final target            : {stats.final_target}")
            print(f"Stop reason             : {stats.stop_reason}")
            print(f"Last event type         : {stats.last_event_type}")
            print(f"Last message source     : {stats.last_message_source}")
            print(f"Last message            : {stats.last_message}")
            print("====================================\n")

            return asdict(stats) if return_dict else stats


def obj_to_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            data = obj.model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            data = obj.dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if hasattr(obj, "to_dict"):
        try:
            data = obj.to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"raw": repr(obj)}


class EventPrinter:
    def __init__(self, run_id: str | None = None, agent_name: str | None = None):
        self.current_type = None
        self.current_call_id = None
        self.current_name = None
        self.buffer = ""
        self.function_call_args = []
        self.function_result_chunks = []
        self.run_id = run_id
        self.agent_name = agent_name or "agent"
        self.messages = []
        self.reasoning = []

    @staticmethod
    def _push_to_webui(run_id: str | None, payload: dict) -> None:
        if not run_id:
            return
        try:
            from core.webui import debug_hub

            debug_hub.record_stream_content(run_id, payload)
        except Exception:
            pass

    def flush(self):
        if not self.current_type or not self.buffer.strip():
            self.current_type = None
            self.current_call_id = None
            self.current_name = None
            self.function_call_args = []
            self.function_result_chunks = []
            self.buffer = ""
            return

        title_map = {
            "text": ("MESSAGE", "\033[96m"),
            "text_reasoning": ("REASONING", "\033[93m"),
            "function_call": ("FUNCTION CALL", "\033[95m"),
            "function_result": ("FUNCTION RESULT", "\033[92m"),
        }

        title, color = title_map.get(self.current_type, (self.current_type.upper(), "\033[97m"))
        reset = "\033[0m"
        bold = "\033[1m"

        if self.current_type == "text":
            self.messages.append(self.buffer.strip())
        elif self.current_type == "text_reasoning":
            self.reasoning.append(self.buffer.strip())

        print(f"\n{bold}{color}========== {title} =========={reset}", flush=True)
        print(f"{color}{self.buffer.strip()}{reset}", flush=True)

        self._push_to_webui(
            self.run_id,
            {
                "type": self.current_type,
                "call_id": self.current_call_id,
                "name": self.current_name,
                "arguments": "".join(self.function_call_args) if self.current_type == "function_call" else None,
                "result": "".join(self.function_result_chunks) if self.current_type == "function_result" else None,
                "text": self.buffer.strip(),
                "agent_name": self.agent_name,
            },
        )

        self.current_type = None
        self.current_call_id = None
        self.current_name = None
        self.function_call_args = []
        self.function_result_chunks = []
        self.buffer = ""

    def handle_content(self, content: Any):
        data = obj_to_dict(content)
        ctype = data.get("type", "unknown")
        call_id = data.get("call_id")

        should_flush = False
        if self.current_type is None:
            self.current_type = ctype
            self.current_call_id = call_id
            if ctype == "function_call":
                self.current_name = data.get("name")
        else:
            if ctype != self.current_type:
                should_flush = True
            elif ctype in {"function_call", "function_result"} and call_id != self.current_call_id:
                should_flush = True

        if should_flush:
            self.flush()
            self.current_type = ctype
            self.current_call_id = call_id
            if ctype == "function_call":
                self.current_name = data.get("name")

        if ctype in {"text", "text_reasoning"}:
            chunk = data.get("text", "")
        elif ctype == "function_call":
            if data.get("name"):
                self.current_name = data.get("name")
            args_part = data.get("arguments")
            if args_part is not None:
                self.function_call_args.append(str(args_part))
            chunk = (
                f"name={self.current_name or data.get('name')}\n"
                f"call_id={call_id}\n"
                f"arguments={''.join(self.function_call_args)}"
            )
        elif ctype == "function_result":
            result_part = data.get("result")
            if result_part is not None:
                self.function_result_chunks.append(str(result_part))
            chunk = (
                f"call_id={call_id}\n"
                f"result={''.join(self.function_result_chunks)}"
            )
        else:
            chunk = str(data)

        if ctype in {"text", "text_reasoning"}:
            self.buffer += chunk
        else:
            if self.buffer:
                self.buffer += "\n"
            self.buffer += chunk

    def handle_update(self, update: Any):
        data = obj_to_dict(update)
        event_type = data.get("type") or type(update).__name__
        ignored_event_types = {"agent_response_update"}
        source = data.get("source", getattr(update, "source", None))
        target = data.get("target", getattr(update, "target", None))

        models_usage = data.get("models_usage", getattr(update, "models_usage", None))
        usage_payload = None
        if models_usage is not None:
            usage_dict = obj_to_dict(models_usage)
            usage_payload = {
                "prompt_tokens": usage_dict.get("prompt_tokens"),
                "completion_tokens": usage_dict.get("completion_tokens"),
                "total_tokens": usage_dict.get("total_tokens"),
            }

        summary_parts = []
        if source or target:
            summary_parts.append(f"{source or '?'} -> {target or '?'}")
        if usage_payload and usage_payload.get("total_tokens"):
            summary_parts.append(f"tokens={usage_payload.get('total_tokens')}")
        if data.get("call_id"):
            summary_parts.append(f"call_id={data.get('call_id')}")

        if str(event_type) not in ignored_event_types:
            self._push_to_webui(
                self.run_id,
                {
                    "type": "update_event",
                    "event_type": event_type,
                    "source": source,
                    "target": target,
                    "call_id": data.get("call_id"),
                    "usage": usage_payload,
                    "summary": " | ".join(summary_parts),
                    "content": data.get("content"),
                    "role": data.get("role"),
                    "name": data.get("name"),
                    "message_id": data.get("message_id"),
                    "contents": data.get("contents"),
                    "agent_name": self.agent_name,
                },
            )

        contents = getattr(update, "contents", None) or data.get("contents")
        if contents:
            for content in contents:
                self.handle_content(content)
        elif data.get("type") in {"text", "text_reasoning", "function_call", "function_result"}:
            self.handle_content(update)
        elif isinstance(data.get("content"), str):
            self.handle_content({"type": "text", "text": data.get("content")})

    def finalize(self):
        self.flush()
