import os
from pathlib import Path

# Disable telemetry before importing libraries that may initialize it at import-time.
os.environ.setdefault("POSTHOG_DISABLED", "true")
os.environ.setdefault("DO_NOT_TRACK", "1")

import cloudscraper
from agent_framework.openai import OpenAIChatClient
from dotenv import load_dotenv
from mem0 import Memory
from openai import OpenAI

from core.utils.logger import create_logger

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(os.path.join(ROOT, ".env"))


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

logger = create_logger("my_app", log_file=os.path.join(ROOT, 'logs', 'my_app.log'))

MAX_LEN_TELEGRAM_MSG = 4096
DEBUG = True

# TELEGRAM
bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
group_general_chat_id = os.getenv("GROUP_GENERAL_CHAT_ID", "-1000000000000").strip()
default_chat_id = group_general_chat_id
single_username = (os.getenv("SINGLE_USERNAME", "default") or "default").strip()
scraper = cloudscraper.create_scraper()

HOST_IP = os.getenv("OLLAMA_HOST_IP", "127.0.0.1")
HOST_PORT = _env_int("OLLAMA_HOST_PORT", 11534)
# Embedding server (Ollama)
OLLAMA_BASE = f"http://{HOST_IP}:{HOST_PORT}"
OLLAMA_SERVER_BASE = os.getenv("OLLAMA_SERVER_BASE", OLLAMA_BASE)

# IMPORTANT:
# Use the MODEL NAME that llama.cpp server exposes
# NOT the .gguf filename
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# OpenAI-compatible client for llama.cpp
client = OpenAI(
    base_url=OLLAMA_BASE + "/v1",
    api_key=os.getenv("LLM_API_KEY", "not-needed"),
)

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": os.getenv("QDRANT_COLLECTION", "agent_memory"),
            "host": os.getenv("QDRANT_HOST", "127.0.0.1"),
            "port": _env_int("QDRANT_PORT", 6333),
            "embedding_model_dims": _env_int("EMBEDDING_MODEL_DIMS", 768),
        }
    },

    # LLM -> llama.cpp
    "llm": {
        "provider": "openai",
        "config": {
            "model": MODEL,
            "openai_base_url": OLLAMA_SERVER_BASE,
            "api_key": os.getenv("LLM_API_KEY", "not-needed"),
        },
    },

    # Embeddings -> Ollama
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": os.getenv("EMBEDDING_MODEL", "embed-cpu:latest"),
            "ollama_base_url": os.getenv("EMBEDDING_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        },
    },
}

# Initialize memory lazily-friendly so skill tools can run without LLM infra.
try:
    memory = Memory.from_config(config)
except Exception as exc:
    memory = None
    logger.warning("Memory initialization skipped: %s", exc)

model_info = {
    "json_output": True,
    "function_calling": True,
    "vision": True,
    "family": "unknown",
    "structured_output": True,
}

client_ollama = OpenAIChatClient(
    base_url=OLLAMA_BASE + "/v1",
    model_id=MODEL,
    api_key=os.getenv("LLM_API_KEY", "not-needed"),
)

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")

if not bot_token:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Telegram bot startup will fail until .env is configured.")
