import sqlite3
from contextlib import closing

from telegram import Bot
from telegram.error import BadRequest, TelegramError
from telegramify_markdown import telegramify, ContentType

from core.utils.config import bot_token


def _require_text(value, field: str, max_len: int = 4096) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text[:max_len]


def _connect(db_path: str) -> sqlite3.Connection:
    if not db_path or not str(db_path).strip():
        raise ValueError("db_path is required")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_topics
        (
            chat_id
            INTEGER
            NOT
            NULL,
            thread_id
            INTEGER
            NOT
            NULL,
            topic_name
            TEXT,
            created_at
            TEXT
            NOT
            NULL
            DEFAULT
            CURRENT_TIMESTAMP,
            updated_at
            TEXT
            NOT
            NULL
            DEFAULT
            CURRENT_TIMESTAMP,
            PRIMARY
            KEY
        (
            chat_id,
            thread_id
        )
            )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_topics_chat_id
            ON chat_topics (chat_id)
        """
    )
    conn.commit()


def add_topic_id(
        db_path: str,
        chat_id: int,
        thread_id: int,
        topic_name: str | None = None,
) -> None:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")
    if not isinstance(thread_id, int):
        raise ValueError("thread_id must be int")

    with closing(_connect(db_path)) as conn:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO chat_topics (chat_id, thread_id, topic_name)
            VALUES (?, ?, ?) ON CONFLICT(chat_id, thread_id) DO
            UPDATE SET
                topic_name = excluded.topic_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, thread_id, topic_name),
        )
        conn.commit()


def remove_topic_id(
        db_path: str,
        chat_id: int,
        thread_id: int,
) -> None:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")
    if not isinstance(thread_id, int):
        raise ValueError("thread_id must be int")

    with closing(_connect(db_path)) as conn:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM chat_topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        conn.commit()


def get_topics_with_names(
        db_path: str,
        chat_id: int,
) -> list[dict]:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")

    with closing(_connect(db_path)) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            """
            SELECT chat_id, thread_id, topic_name, created_at, updated_at
            FROM chat_topics
            WHERE chat_id = ?
            ORDER BY thread_id
            """,
            (chat_id,),
        ).fetchall()

    return [
        {
            "chat_id": int(row["chat_id"]),
            "thread_id": int(row["thread_id"]),
            "name": row["topic_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


async def create_topic(
        chat_id: int,
        name: str,
        first_message: str | None = None,
        db_path: str | None = None,
) -> int:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")

    name = _require_text(name, "name", max_len=128)
    if first_message is not None:
        first_message = _require_text(first_message, "first_message")

    bot = Bot(token=bot_token)

    try:
        result = await bot.create_forum_topic(chat_id=chat_id, name=name)
    except BadRequest as e:
        raise RuntimeError(
            "Telegram create_forum_topic failed. "
            "Make sure this is a forum supergroup with Topics enabled. "
            f"Telegram said: {e}"
        ) from e
    except TelegramError as e:
        raise RuntimeError(f"Telegram error: {e}") from e

    thread_id = int(result.message_thread_id)

    if db_path:
        add_topic_id(db_path, chat_id, thread_id, name)

    if first_message:
        await send_to_topic(chat_id, thread_id, first_message)

    return thread_id


async def send_to_topic(chat_id: int, thread_id: int, text: str) -> None:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")
    if not isinstance(thread_id, int):
        raise ValueError("thread_id must be int")

    text = _require_text(text, "text")
    bot = Bot(token=bot_token)

    try:
        parts = await telegramify(text, max_message_length=4090)

        for item in parts:
            if item.content_type == ContentType.TEXT:
                await bot.send_message(
                    chat_id=chat_id,
                    text=item.text,
                    entities=[e.to_dict() for e in item.entities],
                    message_thread_id=thread_id,
                )

            elif item.content_type == ContentType.PHOTO:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=(item.file_name, item.file_data),
                    caption=item.caption_text or None,
                    caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                    message_thread_id=thread_id,
                )

            elif item.content_type == ContentType.FILE:
                await bot.send_document(
                    chat_id=chat_id,
                    document=(item.file_name, item.file_data),
                    caption=item.caption_text or None,
                    caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                    message_thread_id=thread_id,
                )

    except TelegramError as e:
        raise RuntimeError(f"send_to_topic failed: {e}") from e


async def delete_topic(
        chat_id: int,
        thread_id: int,
        db_path: str | None = None,
) -> None:
    if not isinstance(chat_id, int):
        raise ValueError("chat_id must be int")
    if not isinstance(thread_id, int):
        raise ValueError("thread_id must be int")

    bot = Bot(token=bot_token)

    try:
        await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
    except TelegramError as e:
        raise RuntimeError(f"delete_topic failed: {e}") from e

    if db_path:
        remove_topic_id(db_path, chat_id, thread_id)


async def list_topics(chat_id: int, db_path: str) -> list[dict]:
    return get_topics_with_names(db_path, chat_id)
