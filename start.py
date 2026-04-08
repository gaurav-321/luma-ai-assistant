import os
import warnings

from dotenv import load_dotenv

os.environ.setdefault("POSTHOG_DISABLED", "true")
os.environ.setdefault("DO_NOT_TRACK", "1")


def _configure_runtime_warnings() -> None:
    # qdrant-client + pydantic v2 serializer noise (non-fatal)
    warnings.filterwarnings(
        "ignore",
        message=r"Pydantic serializer warnings:.*",
        category=UserWarning,
        module=r"pydantic\.main",
    )
    # python-telegram-bot internals on Python 3.12+
    warnings.filterwarnings(
        "ignore",
        message=r".*There is no current event loop.*",
        category=DeprecationWarning,
        module=r"telegram\.ext\._application",
    )
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=r"telegram\.ext\._application",
    )
    # websockets legacy deprecation warnings from uvicorn stack
    warnings.filterwarnings(
        "ignore",
        message=r".*websockets\.legacy is deprecated.*",
        category=DeprecationWarning,
        module=r"websockets\.legacy\.__init__",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*websockets\.server\.WebSocketServerProtocol is deprecated.*",
        category=DeprecationWarning,
        module=r"uvicorn\.protocols\.websockets\.websockets_impl",
    )
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=r"websockets\.legacy(\..*)?",
    )
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=r"uvicorn\.protocols\.websockets\.websockets_impl",
    )


_configure_runtime_warnings()
load_dotenv()

from core.telegram_bot import main


if __name__ == "__main__":
    main()
