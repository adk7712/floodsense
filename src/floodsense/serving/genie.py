"""FloodSense - plain-English questions about live risk, answered by a Databricks Genie space.

The Genie space ("FloodSense risk (gold)") reads the gold table, so its answers are about the
live data the pipeline scored, never about a replayed day. The app's "Ask FloodSense" panel calls
``ask`` and shows Genie's text answer, the table its SQL returned and (Analyst view) the SQL.

Auth is the SDK's own, as in ``floodsense.serving.gold``: locally a Databricks CLI profile, in a
Databricks App the credentials it injects (the app needs the Genie space as a resource).
"""

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from floodsense.common.config import settings

TIMEOUT = timedelta(seconds=90)  # a first question took 36 s with a warm warehouse (23 Oct)
MAX_ROWS = 20


class GenieUnavailable(Exception):
    """Genie could not answer (no space configured, sign-in, permissions, timeout, failure)."""


@dataclass
class GenieAnswer:
    conversation_id: str
    text: str | None = None
    description: str | None = None
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)

    @property
    def summary(self) -> str | None:
        """What to show as the answer: Genie's text, or the description of its query."""
        return self.text or self.description


def space_id() -> str | None:
    """The Genie space: ``settings`` first, then a Databricks App's resource env var."""
    return settings.genie_space_id or os.environ.get("GENIE_SPACE_ID") or None


def _client() -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise GenieUnavailable("the databricks-sdk package isn't installed") from exc
    profile = settings.databricks_profile
    try:
        return WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    except Exception as exc:  # no host, no profile, expired login, ...
        raise GenieUnavailable(f"Databricks sign-in failed: {exc}") from exc


def to_answer(message: Any, client: Any) -> GenieAnswer:
    """A finished Genie message -> ``GenieAnswer`` (text, query, first rows of its result)."""
    status = getattr(message.status, "value", message.status)
    if status != "COMPLETED":
        detail = getattr(message.error, "error", None) if message.error else None
        suffix = f": {detail}" if detail else ""
        raise GenieUnavailable(f"Genie stopped with status {status}{suffix}")
    answer = GenieAnswer(conversation_id=message.conversation_id)
    for att in message.attachments or []:
        if att.text and att.text.content and not answer.text:
            answer.text = att.text.content
        if att.query and not answer.sql:
            answer.description = att.query.description
            answer.sql = att.query.query
            try:
                result = client.genie.get_message_attachment_query_result(
                    message.space_id,
                    message.conversation_id,
                    getattr(message, "message_id", None) or message.id,
                    att.attachment_id,
                )
                sr = result.statement_response
                answer.columns = [c.name for c in sr.manifest.schema.columns]
                answer.rows = ((sr.result.data_array if sr.result else None) or [])[:MAX_ROWS]
            except Exception:  # the text answer is still worth showing without the table
                answer.columns, answer.rows = [], []
    if not answer.summary and not answer.sql:
        raise GenieUnavailable("Genie returned no answer")
    return answer


def ask(
    question: str, conversation_id: str | None = None, client: Any | None = None
) -> GenieAnswer:
    """Ask the Genie space ``question`` (a follow-up when ``conversation_id`` is given)."""
    space = space_id()
    if not space:
        raise GenieUnavailable("no Genie space is configured (FLOODSENSE_GENIE_SPACE_ID)")
    w = client or _client()
    try:
        if conversation_id:
            message = w.genie.create_message_and_wait(
                space, conversation_id, question, timeout=TIMEOUT
            )
        else:
            message = w.genie.start_conversation_and_wait(space, question, timeout=TIMEOUT)
    except GenieUnavailable:
        raise
    except TimeoutError as exc:
        raise GenieUnavailable(f"Genie took over {TIMEOUT.seconds} s") from exc
    except Exception as exc:  # network, auth, permissions, unknown space, ...
        raise GenieUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return to_answer(message, w)
