"""Zapbox campaign dispatch survives uncertain outcomes and restarts without resending."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs, unibox
from app.models import EmailLog, QueueSlot, ZapboxLaunchSnapshot, ZapboxSendAttempt
from app.sender import SendFailure, SendResult
from app.time import now
from app.zapbox_preflight import snapshot_hash
from tests.conftest import (
    make_campaign, make_campaign_inbox, make_campaign_lead,
    make_inbox, make_lead, make_sequence,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unknown", "success", "copy_changed"])
async def test_zapbox_dispatch_records_attempt_before_post_and_blocks_repeat(engine, monkeypatch, outcome):
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        inbox = await make_inbox(session, email="sender@example.test", provider="zapbox")
        campaign = await make_campaign(session, sending_days=list(range(7)),
                                       sending_hours_start="00:00", sending_hours_end="23:59")
        lead = await make_lead(session, email="owned@example.test")
        enrollment = await make_campaign_lead(session, campaign.id, lead.id)
        await make_campaign_inbox(session, campaign.id, inbox.id)
        await make_sequence(session, campaign.id, subject="Pilot", body="Hello {{name}}")
        slot = QueueSlot(campaign_lead_id=enrollment.id, inbox_id=inbox.id,
                         sequence_index=0, scheduled_date=now() - timedelta(minutes=1),
                         position_in_day=1)
        session.add(slot)
        await session.flush()
        digest = await snapshot_hash(session, enrollment, lead, campaign)
        session.add(ZapboxLaunchSnapshot(campaign_lead_id=enrollment.id, content_hash=digest))
        slot_id = slot.id
        inbox_id = inbox.id
        enrollment_id = enrollment.id
        await session.commit()

    if outcome == "copy_changed":
        async with session_factory() as session:
            changed_lead = (await session.execute(select(jobs.Lead).where(
                jobs.Lead.id == lead.id,
            ))).scalar_one()
            changed_lead.name = "Changed Name"
            await session.commit()

    async def synced(*args, **kwargs):
        return True

    post = Mock(return_value=(
        SendFailure("unknown", "Zapbox send outcome needs reconciliation")
        if outcome == "unknown" else SendResult("<sent@example.test>", "thread-one")
    ))
    monkeypatch.setattr(jobs, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(unibox, "sync_single_inbox", synced)
    monkeypatch.setattr(jobs, "send_email", post)
    monkeypatch.setattr(jobs.settings, "test_mode", False)

    await jobs.send_slot_job(slot_id)
    assert post.call_count == (0 if outcome == "copy_changed" else 1)

    async with session_factory() as session:
        attempt = (await session.execute(select(ZapboxSendAttempt).where(
            ZapboxSendAttempt.campaign_lead_id == enrollment_id,
        ))).scalar_one_or_none()
        inbox = (await session.execute(select(jobs.Inbox).where(jobs.Inbox.id == inbox_id))).scalar_one()
        if outcome == "copy_changed":
            assert attempt is None
            assert inbox.paused is False
            assert (await session.execute(select(QueueSlot).where(
                QueueSlot.id == slot_id,
            ))).scalar_one_or_none() is not None
            return
        assert attempt.status == ("unknown" if outcome == "unknown" else "sent")
        assert inbox.paused is (outcome == "unknown")
        if outcome == "unknown":
            assert (await session.execute(select(EmailLog).where(
                EmailLog.inbox_id == inbox_id,
            ))).scalar_one_or_none() is None
        else:
            assert attempt.thread_id == "thread-one"

    # Each invocation opens a fresh DB session, matching restart behavior.
    await jobs.send_slot_job(slot_id)
    assert post.call_count == 1
