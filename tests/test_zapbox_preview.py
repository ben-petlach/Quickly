"""The campaign preview must show missing contact variables before a pilot send."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers.campaigns import PreviewRequest, _zapbox_launch_check, preview_email
from app.zapbox_preflight import snapshot_report


def test_preview_renders_contact_fields_and_reports_missing_fields():
    sequence = SimpleNamespace(
        id=1,
        campaign_id=2,
        subject="Pilot for {{firstName}}",
        body="Hi {{firstName}}, {{missingField}}",
        is_html=False,
        position=0,
    )
    campaign = SimpleNamespace(track_clicks=False, track_opens=False)
    lead = SimpleNamespace(name="Ben", email="owned@example.com", custom_data={"firstName": "Ben"})
    db = SimpleNamespace(execute=AsyncMock())
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: sequence),
        SimpleNamespace(scalar_one_or_none=lambda: campaign),
        SimpleNamespace(scalar_one_or_none=lambda: lead),
    ]

    preview = asyncio.run(preview_email(2, PreviewRequest(sequence_id=1, lead_id=3), db))

    assert preview["subject"] == "Pilot for Ben"
    assert preview["body"] == "Hi Ben, {{missingField}}"
    assert preview["missing_placeholders"] == ["missingField"]


def test_launch_check_covers_every_step_and_freezes_rendered_copy():
    sequences = [
        SimpleNamespace(id=1, position=0, subject="Hi {{firstName}}", body="{{product}}",
                        preview_text="", is_html=False, sequence_type="standard", variants=[]),
        SimpleNamespace(id=2, position=1, subject="", body="{{missingField}}",
                        preview_text="", is_html=False, sequence_type="standard", variants=[]),
    ]
    enrollment = SimpleNamespace(id=3)
    lead = SimpleNamespace(name="Ben", email="owned@example.com",
                           custom_data={"firstName": "Ben", "product": "drawings"})
    campaign = SimpleNamespace(id=4, track_opens=False, track_clicks=False,
                               send_first_as_text=False, send_all_as_text=False,
                               add_unsubscribe_header=False, add_one_click_unsubscribe=False)
    db = SimpleNamespace(execute=AsyncMock())

    def result(rows):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    db.execute.side_effect = [result(sequences), result([]), result(sequences), result([])]
    first_hash, issues = asyncio.run(snapshot_report(db, enrollment, lead, campaign))
    assert issues == [{"step": 2, "variant": "default", "fields": ["missingField"]}]

    lead.custom_data["product"] = "updated drawings"
    second_hash, _ = asyncio.run(snapshot_report(db, enrollment, lead, campaign))
    assert first_hash != second_hash

    lead.custom_data["product"] = ""
    db.execute.side_effect = [result(sequences), result([])]
    _, empty_issues = asyncio.run(snapshot_report(db, enrollment, lead, campaign))
    assert empty_issues[0] == {"step": 1, "variant": "default", "fields": ["product"]}


def test_launch_report_identifies_contact_and_step():
    campaign = SimpleNamespace(id=4, track_opens=False, track_clicks=False,
                               send_first_as_text=False, send_all_as_text=False,
                               add_unsubscribe_header=False, add_one_click_unsubscribe=False)
    enrollment = SimpleNamespace(id=3)
    lead = SimpleNamespace(id=5, name="Ben", email="owned@example.com", custom_data={})
    sequence = SimpleNamespace(id=1, position=0, subject="Hi", body="{{product}}",
                               preview_text="", is_html=False, sequence_type="standard", variants=[])
    db = SimpleNamespace(execute=AsyncMock())
    db.execute.side_effect = [
        SimpleNamespace(scalar_one=lambda: campaign),
        SimpleNamespace(all=lambda: [(enrollment, lead)]),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [sequence])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]

    approved, issues = asyncio.run(_zapbox_launch_check(db, campaign.id))
    assert len(approved) == 1
    assert issues == [{"lead_id": 5, "email": "owned@example.com", "step": 1,
                       "variant": "default", "fields": ["product"]}]
