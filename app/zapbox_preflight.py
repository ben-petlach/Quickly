"""Freeze rendered campaign copy when a Zapbox campaign is launched."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import CustomEmailOverride, Sequence
from app.sender import get_lead_data, render_body, unresolved_placeholders


async def snapshot_report(session, campaign_lead, lead, campaign):
    """Return a stable copy hash and all missing variables for one enrollment."""
    sequences = (
        await session.execute(
            select(Sequence)
            .options(selectinload(Sequence.variants))
            .where(Sequence.campaign_id == campaign.id)
            .order_by(Sequence.position)
        )
    ).scalars().all()
    overrides = (
        await session.execute(
            select(CustomEmailOverride).where(
                CustomEmailOverride.campaign_lead_id == campaign_lead.id
            )
        )
    ).scalars().all()
    override_by_sequence = {row.sequence_id: row for row in overrides}
    lead_data = get_lead_data(lead)
    lead_data["unsubscribe_link"] = "<unsubscribe link>"
    content = []
    issues = []

    for sequence in sequences:
        subject = sequence.subject or ""
        body = sequence.body or ""
        preview_text = sequence.preview_text or ""
        is_html = sequence.is_html
        if sequence.sequence_type == "personalized":
            override = override_by_sequence.get(sequence.id)
            subject = (override.subject if override and override.subject is not None
                       else sequence.fallback_subject or subject)
            body = (override.body if override and override.body is not None
                    else sequence.fallback_body or body)
            if override and override.is_html is not None:
                is_html = override.is_html

        choices = [("default", subject, body, preview_text, is_html)]
        for variant in sequence.variants:
            if variant.enabled:
                choices.append((
                    variant.label or str(variant.id),
                    variant.subject if variant.subject is not None else subject,
                    variant.body,
                    variant.preview_text if variant.preview_text is not None else preview_text,
                    variant.is_html if variant.is_html is not None else is_html,
                ))

        for label, choice_subject, choice_body, choice_preview, choice_html in choices:
            source = "\n".join((choice_subject, choice_body, choice_preview))
            if "{{random:" in source:
                issues.append({"step": sequence.position + 1, "variant": label,
                               "fields": ["random (copy cannot be frozen)"]})
            rendered_subject = render_body(choice_subject, lead_data)
            rendered_body = render_body(choice_body, lead_data)
            rendered_preview = render_body(choice_preview, lead_data)
            missing = set(unresolved_placeholders(rendered_subject, rendered_body + "\n" + rendered_preview))
            for field in unresolved_placeholders(choice_subject, choice_body + "\n" + choice_preview):
                if field in lead_data and lead_data[field] in (None, ""):
                    missing.add(field)
            if missing:
                issues.append({"step": sequence.position + 1, "variant": label, "fields": sorted(missing)})
            content.append((sequence.position, label, rendered_subject, rendered_body,
                            rendered_preview, choice_html))

    if not sequences:
        issues.append({"step": 0, "variant": "default", "fields": ["sequence missing"]})
    approved = {"to": lead.email, "content": content,
                "track_opens": campaign.track_opens, "track_clicks": campaign.track_clicks,
                "send_first_as_text": campaign.send_first_as_text,
                "send_all_as_text": campaign.send_all_as_text,
                "add_unsubscribe_header": campaign.add_unsubscribe_header,
                "add_one_click_unsubscribe": campaign.add_one_click_unsubscribe}
    encoded = json.dumps(approved, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), issues


async def snapshot_hash(session, campaign_lead, lead, campaign) -> str:
    digest, issues = await snapshot_report(session, campaign_lead, lead, campaign)
    if issues:
        raise ValueError(f"Zapbox copy has unresolved fields: {issues}")
    return digest
