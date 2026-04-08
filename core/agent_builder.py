import importlib.util
import inspect
import json
import shutil
import traceback
from pathlib import Path
from textwrap import dedent
from typing import Any

from agent_framework import Skill, SkillsProvider

from core.utils.config import ROOT, client_ollama, single_username
from core.utils.debug import EventPrinter


def get_final_text(final_response: Any) -> str:
    data = _to_dict(final_response)
    messages = data.get("messages", [])

    extracted: list[str] = []
    for msg in messages:
        msg_dict = _to_dict(msg)
        content = msg_dict.get("content")
        extracted.extend(_extract_text_from_content(content))

    for text in reversed(extracted):
        if text.strip():
            return text.strip()

    return ""


def _extract_text_from_content(content: Any) -> list[str]:
    texts: list[str] = []

    if content is None:
        return texts

    if isinstance(content, str):
        text = content.strip()
        if text and text != "Msg successfully sent":
            texts.append(text)
        return texts

    if isinstance(content, list):
        for item in content:
            texts.extend(_extract_text_from_content(item))
        return texts

    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            text = text.strip()
            if text and text != "Msg successfully sent":
                texts.append(text)

        contents = content.get("contents")
        if isinstance(contents, list):
            for item in contents:
                texts.extend(_extract_text_from_content(item))

        return texts

    return _extract_text_from_content(_to_dict(content))


def _to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}

    if isinstance(obj, dict):
        return obj

    for method_name in ("to_dict", "model_dump", "dict"):
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                data = method()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    if hasattr(obj, "__dict__"):
        try:
            return dict(vars(obj))
        except Exception:
            pass

    return {}


class AgentBuilder:
    _module_cache: dict[str, Any] = {}
    SINGLE_USERNAME = single_username

    def __init__(
            self,
            chat_id: int | str,
            username: str,
            current_topic: str = "general",
            agent_md_file: str | None = "manager.md",
            debug_agent_name: str | None = None,
            response_format=None,
            skills: bool = True,
            memory_contexts: list[Any] | None = None,
            think=False,
            allow_skills=True,
            trace_id: str | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.current_topic = current_topic
        self.username = self._sanitize_username(username)
        self.memory_contexts = memory_contexts
        self.final_reply_text: str | None = None
        self.used_skills: list[dict[str, str]] = []
        user_dir = self.init_new_user(self.username)
        self.think = think
        self.user_md = (user_dir / "user.md").read_text(encoding="utf-8")
        self.agent_md = (user_dir / agent_md_file).read_text(encoding="utf-8") if agent_md_file else ""
        self.debug_agent_name = debug_agent_name or (Path(agent_md_file).stem if agent_md_file else "agent")
        self.user_db = str(user_dir / "data.sqlite")
        self.response_format = response_format
        self.enable_skills = skills
        self.allow_skills_execution = allow_skills
        self.trace_id = trace_id

        self.skills_provider = SkillsProvider(
            skill_paths=[
                ROOT / "skills" / "personal",
                ROOT / "skills" / "work",
            ],
            script_runner=self.script_runner,

        )
        self.blocked_tools = (
            ("read_skill_resource", "run_skill_script")
            if not self.allow_skills_execution
            else ("read_skill_resource",)
        )

        if hasattr(self.skills_provider, "_tools"):
            self.skills_provider._tools = [
                t for t in self.skills_provider._tools
                if getattr(t, "name", None) not in self.blocked_tools
            ]

        print([getattr(t, "name", None) for t in self.skills_provider._tools])

        self.agent = self.build_agent()

    @staticmethod
    def _sanitize_username(username: str | None) -> str:
        return AgentBuilder.SINGLE_USERNAME

    @classmethod
    def init_new_user(cls, username: str) -> Path:
        users_root = ROOT / "users"
        default_dir = users_root / cls.SINGLE_USERNAME
        user_dir = default_dir

        users_root.mkdir(parents=True, exist_ok=True)

        if user_dir.exists():
            return user_dir

        user_dir.mkdir(parents=True, exist_ok=True)

        default_agent = default_dir / "manager.md"
        default_user = default_dir / "user.md"
        default_db = default_dir / "data.sqlite"

        if default_agent.exists():
            shutil.copy2(default_agent, user_dir / "manager.md")
        else:
            (user_dir / "manager.md").write_text(
                "# Manager\n\nYou are the Manager agent.",
                encoding="utf-8",
            )

        if default_user.exists():
            shutil.copy2(default_user, user_dir / "user.md")
        else:
            (user_dir / "user.md").write_text(
                f"# User\n\nUsername: {username}\n",
                encoding="utf-8",
            )

        if default_db.exists():
            shutil.copy2(default_db, user_dir / "data.sqlite")

        return user_dir

    def init_yaml(self):
        # Single-user mode: no details.yaml file is maintained.
        return None

    def build_agent(self):
        instructions = "\n\n".join([self.agent_md, self.user_md]).strip() if self.allow_skills_execution else self.agent_md
        tools = [self.reply_to_user]
        return client_ollama.as_agent(
            name="Manager",
            instructions=instructions,
            tools=tools,
            context_providers=[self.skills_provider] if self.enable_skills else None,
            response_format=self.response_format,
            default_options={
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": self.think}
                }
            },
        )

    async def reply_to_user(
            self,
            text: str,
    ) -> dict[str, Any]:
        """Capture final user-facing reply from the manager tool call.

        This does not send a Telegram message directly. Delivery remains in reply_queue/reply_loop.
        """
        text_value = (text or "").strip()
        if not text_value:
            return {"ok": False, "error": "text is required"}

        self.final_reply_text = text_value
        return {
            "ok": True,
            "captured": True,
            "message": "Final reply captured",
        }

    def get_captured_reply(self) -> str | None:
        return self.final_reply_text

    def get_used_skills_summary(self) -> str:
        if not self.used_skills:
            return "Skill used: none - answered directly without running a skill."

        unique: dict[str, set[str]] = {}
        for item in self.used_skills:
            skill_name = (item.get("skill") or "").strip() or "unknown"
            operation = (item.get("operation") or "").strip()
            unique.setdefault(skill_name, set())
            if operation:
                unique[skill_name].add(operation)

        chunks: list[str] = []
        for skill_name, operations in unique.items():
            if operations:
                ops = ", ".join(sorted(operations)[:3])
                chunks.append(f"{skill_name} ({ops})")
            else:
                chunks.append(skill_name)
        return f"Skill used: {'; '.join(chunks)}."

    def _load_skill_module(self, script_path: Path):
        cache_key = str(script_path.resolve())
        if cache_key in self._module_cache:
            return self._module_cache[cache_key]

        module_name = f"skill_module_{abs(hash(cache_key))}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module spec for {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._module_cache[cache_key] = module
        return module

    async def script_runner(
            self,
            skill: Skill,
            script_name: str | None = None,
            args: dict | str | None = None,
            arguments: dict | str | None = None,
            **kwargs,
    ):
        script_file = "tool.py"
        script_path = Path(skill.path) / script_file

        raw_args = arguments if arguments is not None else args
        raw_args = raw_args or {}

        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                return {"ok": False, "error": "Arguments must be valid JSON."}

        if not isinstance(raw_args, dict):
            return {"ok": False, "error": "Arguments must resolve to a dictionary."}

        print("\n[DEBUG] script_runner invoked")
        print("[DEBUG] skill:", skill.name)
        print("[DEBUG] script_path:", script_path)
        print("[DEBUG] raw_args:", raw_args)
        print("[DEBUG] kwargs:", kwargs)

        if not script_path.exists():
            return {"ok": False, "error": f"Script not found: {script_path}"}

        try:
            operation_name = ""
            if isinstance(raw_args, dict):
                op_val = raw_args.get("operation")
                if op_val is not None:
                    operation_name = str(op_val)
            self.used_skills.append({"skill": skill.name, "operation": operation_name})

            module = self._load_skill_module(script_path)
            process_args = getattr(module, "process_args", None)

            if process_args is None:
                raise RuntimeError(
                    f"{script_path} does not define required function `process_args(...)`"
                )

            extra_args = {
                "db_path": self.user_db,
                "username": self.username,
                "chat_id": self.chat_id,
                "current_topic": self.current_topic,
            }

            try:
                result = process_args(args=raw_args, extra_args=extra_args)
            except TypeError:
                result = process_args(raw_args, extra_args)

            if inspect.isawaitable(result):
                result = await result

            print("[DEBUG] result:", result)
            return result

        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    async def run_query(
            self,
            query: str,
            *,
            stream: bool = True,
            options: dict | None = None,
    ):
        query = "\n".join(line.strip() for line in dedent(query).splitlines()).strip()
        options = dict(options or {})
        printer = EventPrinter(run_id=self.trace_id, agent_name=self.debug_agent_name)
        print(query)
        if self.response_format is not None and "response_format" not in options:
            options["response_format"] = self.response_format

        # Structured-output path: stream first for richer debug telemetry, fallback to non-streaming.
        if self.response_format is not None:
            stream_result = self.agent.run(query, stream=True, options=options, max_iterations=10)
            response = None
            try:
                async for msg in stream_result:
                    printer.handle_update(msg)
            except Exception as e:
                print(f"[STRUCTURED STREAM ERROR] {e}")
            finally:
                printer.finalize()

            try:
                response = await stream_result.get_final_response()
            except Exception as e:
                print(f"[STRUCTURED FINAL RESPONSE ERROR] {e}")

            if response is None:
                try:
                    response = await self.agent.run(query, options=options, )
                except Exception as e:
                    print(f"[RUN ERROR] {e}")
                    raise

            if getattr(response, "value", None) is not None:
                print("\n========== STRUCTURED FINAL ==========")
                print(response.value)
                return response.value

            final_text = getattr(response, "text", "") or ""
            if not final_text:
                final_text = get_final_text(response).strip()
            return final_text

        # Normal text streaming path.
        result = self.agent.run(query, stream=stream, options=options)

        print("\n========== STREAM OUTPUT ==========")

        try:
            async for msg in result:
                printer.handle_update(msg)
        except Exception as e:
            print(f"[STREAM ERROR] {e}")
        finally:
            printer.finalize()

        final_text = ""
        if printer.messages:
            final_text = printer.messages[-1].strip()

        if not final_text:
            try:
                final = await result.get_final_response()
                final_text = get_final_text(final).strip()
            except Exception as e:
                print(f"[FINAL RESPONSE ERROR] {e}")
                final_text = ""

        print("\n========== CLEAN FINAL ==========")
        print(final_text or "<no final text found>")

        return final_text
