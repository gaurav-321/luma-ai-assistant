import re

MDV2_SPECIALS = r'[]()~`>#+-=|{}.!'

_escape_regex = re.compile(f'([{re.escape(MDV2_SPECIALS)}])')
MAX_LEN_TELEGRAM_MSG = 4096


def escape_markdown_v2(text: str) -> str:
    # escape existing backslashes first
    text = text.replace("\\", "\\\\")
    return _escape_regex.sub(r'\\\1', text)


def format_msg(text: str) -> str:
    return escape_markdown_v2(text)


def split_reply_text(text: str, pattern=r"\n"):
    text = escape_markdown_v2(text)
    if len(text) <= MAX_LEN_TELEGRAM_MSG:
        return [text]

    chunks = []
    lines = re.split(pattern, text)

    current = ""

    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_LEN_TELEGRAM_MSG:
            if current:
                chunks.append(current)
                current = line
            else:
                # single line too big → hard split
                for i in range(0, len(line), MAX_LEN_TELEGRAM_MSG):
                    chunks.append(line[i:i + MAX_LEN_TELEGRAM_MSG])
                current = ""
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks
