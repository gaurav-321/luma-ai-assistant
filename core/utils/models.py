from pydantic import BaseModel


class WatcherDecision(BaseModel):
    should_reply: bool
    reply_text: str = ""
    reason: str = ""
