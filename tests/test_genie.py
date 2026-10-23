"""Ask FloodSense: Genie answers turned into what the app shows (offline, fake client)."""

from types import SimpleNamespace as NS

import pytest

from floodsense.serving import genie


def _message(status="COMPLETED", attachments=(), error=None):
    return NS(
        status=NS(value=status),
        error=error,
        conversation_id="conv1",
        space_id="space1",
        message_id="msg1",
        id="msg1",
        attachments=list(attachments),
    )


def _query_att():
    return NS(
        attachment_id="att1",
        text=None,
        query=NS(description="Areas at Moderate or High risk", query="SELECT zone FROM gold"),
    )


def _text_att(content):
    return NS(attachment_id="att2", text=NS(content=content), query=None)


class FakeGenie:
    def __init__(self, message, rows=(("BEDOK", 0.012),), fail=None):
        self.message, self.rows, self.fail, self.calls = message, rows, fail, []

    def start_conversation_and_wait(self, space, question, timeout):
        self.calls.append(("start", space, question))
        if self.fail:
            raise self.fail
        return self.message

    def create_message_and_wait(self, space, conversation_id, question, timeout):
        self.calls.append(("follow", conversation_id, question))
        return self.message

    def get_message_attachment_query_result(self, space, conv, msg, att):
        cols = [NS(name="zone"), NS(name="probability")]
        return NS(
            statement_response=NS(
                manifest=NS(schema=NS(columns=cols)),
                result=NS(data_array=[list(r) for r in self.rows]),
            )
        )


def _client(fake):
    return NS(genie=fake)


def test_answer_has_text_table_and_sql():
    fake = FakeGenie(_message(attachments=[_query_att(), _text_att("Bedok is at Moderate.")]))
    answer = genie.ask("Which areas are at risk?", client=_client(fake))
    assert answer.summary == "Bedok is at Moderate."
    assert answer.sql == "SELECT zone FROM gold"
    assert answer.columns == ["zone", "probability"]
    assert answer.rows == [["BEDOK", 0.012]]
    assert fake.calls[0][0] == "start"


def test_query_description_is_the_fallback_summary():
    fake = FakeGenie(_message(attachments=[_query_att()]))
    assert genie.ask("q", client=_client(fake)).summary == "Areas at Moderate or High risk"


def test_follow_up_continues_the_conversation():
    fake = FakeGenie(_message(attachments=[_text_att("ok")]))
    genie.ask("and now?", conversation_id="conv1", client=_client(fake))
    assert fake.calls == [("follow", "conv1", "and now?")]


def test_rows_are_capped():
    rows = [(f"Z{i}", 0.0) for i in range(50)]
    fake = FakeGenie(_message(attachments=[_query_att()]), rows=rows)
    assert len(genie.ask("q", client=_client(fake)).rows) == genie.MAX_ROWS


@pytest.mark.parametrize(
    "fake",
    [
        FakeGenie(_message(status="FAILED", error=NS(error="boom"))),
        FakeGenie(_message(attachments=[])),
        FakeGenie(_message(), fail=TimeoutError()),
        FakeGenie(_message(), fail=RuntimeError("403")),
    ],
)
def test_failures_become_genie_unavailable(fake):
    with pytest.raises(genie.GenieUnavailable):
        genie.ask("q", client=_client(fake))


def test_no_space_configured(monkeypatch):
    monkeypatch.setattr(genie.settings, "genie_space_id", None)
    monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
    with pytest.raises(genie.GenieUnavailable):
        genie.ask("q", client=_client(FakeGenie(_message())))
