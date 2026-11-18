from unittest.mock import AsyncMock
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import Campaign, CampaignInbox, Inbox, Sequence
from app.queue_logic import _next_available_send_time_today
from app.routers import inbox as inbox_router
from app.schemas import ConnectUrlRequest, InboxCreate, InboxUpdate


def test_inbox_wait_interval_rejects_zero_on_create_and_update():
    with pytest.raises(ValidationError):
        InboxCreate(email="sender@example.com", wait_minutes_between=0)
    with pytest.raises(ValidationError):
        InboxUpdate(wait_minutes_between=0)
    with pytest.raises(ValidationError):
        ConnectUrlRequest(wait_minutes_between=0)


@pytest.mark.asyncio
async def test_legacy_zero_wait_interval_does_not_break_queue():
    day = date(2026, 11, 18)
    result = await _next_available_send_time_today(
        session=None,
        inbox_id=1,
        day=day,
        sending_start="09:00",
        sending_end="10:00",
        wait_minutes=0,
        now=datetime(2026, 11, 18, 8, 0),
        cache={("_preseeded",): True},
    )
    assert result == datetime(2026, 11, 18, 9, 0)


@pytest.mark.asyncio
async def test_zapbox_mailbox_must_be_connected(session, monkeypatch):
    accounts = [
        {"emailAccount": "sender@example.com", "firstName": "Test", "lastName": "Sender", "status": "active"},
        {"emailAccount": "paused@example.com", "firstName": "Paused", "lastName": "Sender", "status": "paused"},
    ]
    monkeypatch.setattr("app.zapbox.list_accounts", AsyncMock(return_value=accounts))

    listed = await inbox_router.list_zapbox_accounts()
    assert listed[0] == {"email": "sender@example.com", "display_name": "Test Sender", "status": "active"}

    with pytest.raises(HTTPException) as error:
        await inbox_router.create_inbox(InboxCreate(email="other@example.com", provider="zapbox"), db=session)
    assert error.value.status_code == 422

    with pytest.raises(HTTPException) as error:
        await inbox_router.create_inbox(InboxCreate(email="paused@example.com", provider="zapbox"), db=session)
    assert error.value.status_code == 422

    inbox = await inbox_router.create_inbox(InboxCreate(email="sender@example.com", provider="zapbox"), db=session)
    assert inbox.provider == "zapbox"


@pytest.mark.asyncio
async def test_zapbox_preview_test_send_is_blocked_before_transport(session, monkeypatch):
    from app.routers import campaigns as campaigns_router
    from app.routers.campaigns import TestEmailRequest

    campaign = Campaign(name="Pilot", sending_days=[], timezone="UTC")
    inbox = Inbox(email="sender@example.com", provider="zapbox")
    session.add_all((campaign, inbox))
    await session.flush()
    session.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox.id, position=0))
    sequence = Sequence(campaign_id=campaign.id, position=0, subject="Test", body="Hello")
    session.add(sequence)
    await session.flush()

    def unexpected_send(**kwargs):
        raise AssertionError("Transport was called")

    monkeypatch.setattr("app.sender.send_email", unexpected_send)
    with pytest.raises(HTTPException) as error:
        await campaigns_router.send_test_email(
            campaign.id,
            TestEmailRequest(sequence_id=sequence.id, to_email="recipient@example.com"),
            db=session,
        )
    assert error.value.status_code == 422
