from types import SimpleNamespace
from datetime import datetime

import pytest
from sqlalchemy import select

from app import unibox
from app import zapbox
from app.models import EmailLog, LeadReply, QueueSlot
from app.routers.campaigns import get_campaign
from tests.conftest import make_campaign, make_campaign_lead, make_inbox, make_lead


class _Rows:
    def all(self):
        return [("thread-1", "owner@example.test", "sent-1")]


class _Database:
    async def execute(self, statement):
        return _Rows()


@pytest.mark.asyncio
async def test_zapbox_sync_only_records_matching_campaign_reply(monkeypatch):
    messages = [
        {
            "threadId": "thread-1",
            "messageId": "sent-1",
            "from": [{"address": "sender@example.test"}],
            "to": [{"address": "owner@example.test"}],
        },
        {
            "threadId": "thread-1",
            "messageId": "reply-1",
            "from": [{"address": "owner@example.test"}],
            "to": [{"address": "sender@example.test"}],
            "date": "2026-11-18T14:00:00Z",
            "subject": "Re: test",
            "body": {"plain": "Reply received", "html": ""},
        },
    ]
    recorded = []

    async def get_thread(account, thread_id):
        assert account == "sender@example.test"
        assert thread_id == "thread-1"
        return messages

    async def record(db, **kwargs):
        recorded.append(kwargs)
        return None, True, (kwargs["inbox"].id, kwargs["thread_key"])

    monkeypatch.setattr(zapbox, "get_thread", get_thread)
    monkeypatch.setattr(unibox, "_process_smtp_inbound_message", record)
    touched = await unibox._sync_inbox_zapbox(
        _Database(),
        SimpleNamespace(id=7, email="sender@example.test"),
    )

    assert touched == {(7, "thread-1")}
    assert len(recorded) == 1
    assert recorded[0]["parsed"]["message_id"] == "reply-1"
    assert recorded[0]["parsed"]["from"] == "owner@example.test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"threadId": "thread-1", "messageId": "reply-1",
          "from": [{"address": "owner@example.test"}]}],
        [{"threadId": "thread-1", "messageId": "sent-1",
          "from": [{"address": "other@example.test"}],
          "to": [{"address": "owner@example.test"}]}],
        [{"threadId": "thread-1", "messageId": "sent-1",
          "from": [{"address": "sender@example.test"}],
          "to": [{"address": "other@example.test"}]}],
        [{"threadId": "other-thread", "messageId": "sent-1",
          "from": [{"address": "sender@example.test"}],
          "to": [{"address": "owner@example.test"}]}],
    ],
)
async def test_zapbox_sync_holds_when_original_send_is_not_visible(monkeypatch, messages):
    async def get_thread(account, thread_id):
        return messages

    monkeypatch.setattr(zapbox, "get_thread", get_thread)
    with pytest.raises(ValueError, match="Zapbox thread"):
        await unibox._sync_inbox_zapbox(
            _Database(),
            SimpleNamespace(id=7, email="sender@example.test"),
        )


@pytest.mark.asyncio
async def test_zapbox_reply_stops_followup_and_appears_in_stats(session, monkeypatch):
    inbox = await make_inbox(session, email="sender@example.test", provider="zapbox")
    campaign = await make_campaign(session)
    replied = await make_lead(session, email="reply@example.test")
    silent = await make_lead(session, email="silent@example.test")
    replied_enrollment = await make_campaign_lead(session, campaign.id, replied.id)
    await make_campaign_lead(session, campaign.id, silent.id)
    session.add_all([
        EmailLog(lead_id=replied.id, campaign_id=campaign.id, inbox_id=inbox.id,
                 thread_id="reply-thread", message_id="sent-reply"),
        EmailLog(lead_id=silent.id, campaign_id=campaign.id, inbox_id=inbox.id,
                 thread_id="silent-thread", message_id="sent-silent"),
        QueueSlot(campaign_lead_id=replied_enrollment.id, inbox_id=inbox.id,
                  sequence_index=1, scheduled_date=datetime(2026, 11, 19, 9),
                  position_in_day=1),
    ])
    await session.flush()

    async def get_thread(account, thread_id):
        recipient = "reply@example.test" if thread_id == "reply-thread" else "silent@example.test"
        messages = [{
            "threadId": thread_id, "messageId": f"sent-{thread_id.split('-')[0]}",
            "from": [{"address": "sender@example.test"}],
            "to": [{"address": recipient}],
        }]
        if thread_id == "reply-thread":
            messages.append({
                "threadId": thread_id, "messageId": "inbound-reply",
                "from": [{"address": recipient}],
                "to": [{"address": "sender@example.test"}],
                "date": "2026-11-18T14:00:00Z",
                "subject": "Re: hello",
                "body": {"plain": "Interested", "html": ""},
            })
        return messages

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(zapbox, "get_thread", get_thread)
    monkeypatch.setattr(unibox, "_fire_lead_reply_webhook_bg", no_op)
    monkeypatch.setattr(unibox, "_classify_and_notify_bg", no_op)
    monkeypatch.setattr(unibox.unibox_events, "publish", no_op)
    touched = await unibox._sync_inbox_zapbox(session, inbox)
    assert touched == {(inbox.id, "reply-thread")}
    reply = (await session.execute(select(LeadReply).where(
        LeadReply.lead_id == replied.id,
        LeadReply.campaign_id == campaign.id,
    ))).scalar_one_or_none()
    assert reply is not None
    assert (await session.execute(select(QueueSlot).where(
        QueueSlot.campaign_lead_id == replied_enrollment.id,
    ))).scalar_one_or_none() is None
    conversations = await unibox.list_unibox_conversations(session, page=1, page_size=10)
    assert conversations["items"][0]["provider"] == "zapbox"
    assert conversations["items"][0]["thread_id"] == "reply-thread"
    campaign_page = await get_campaign(campaign.id, db=session)
    assert campaign_page.stats.replies == 1
