"""Safety checks for the Zapbox campaign transport."""

from unittest.mock import patch

import httpx
import pytest

from app.sender import zapbox_html_body
from app.zapbox import ZapboxAmbiguousSend, ZapboxError, send_message


def _pilot_env(monkeypatch):
    monkeypatch.setenv("ZAPBOX_API_KEY", "test-key")
    monkeypatch.setenv("ZAPBOX_WORKSPACE_ID", "test-workspace")
    monkeypatch.setenv("ZAPBOX_PILOT_SENDER", "sender@example.org")
    monkeypatch.setenv("ZAPBOX_PILOT_RECIPIENTS", "reply@example.org,silent@example.org")


def test_send_blocks_other_recipient_before_network(monkeypatch):
    _pilot_env(monkeypatch)
    with patch("httpx.Client.post") as post:
        with pytest.raises(ZapboxError, match="allowlist"):
            send_message(account="sender@example.org", to="prospect@example.org",
                         subject="Pilot", body="<p>Hello</p>")
        post.assert_not_called()


def test_send_records_message_and_thread(monkeypatch):
    _pilot_env(monkeypatch)
    response = httpx.Response(200, json={"success": True, "data": {
        "messageId": "<one@example.org>", "threadId": "thread-one",
    }}, request=httpx.Request("POST", "https://api.zapmail.ai/api/v2/onebox/send-email"))
    with patch("httpx.Client.post", return_value=response) as post:
        result = send_message(account="sender@example.org", to="silent@example.org",
                              subject="Pilot", body="<p>Hello</p>")
    assert (result["messageId"], result["threadId"]) == ("<one@example.org>", "thread-one")
    assert post.call_args.kwargs["json"]["to"] == "silent@example.org"


def test_timeout_is_ambiguous_and_plain_body_is_safe(monkeypatch):
    _pilot_env(monkeypatch)
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(ZapboxAmbiguousSend):
            send_message(account="sender@example.org", to="reply@example.org",
                         subject="Pilot", body="<p>Hello</p>")
    assert zapbox_html_body("A < B\nSecond", False) == "<div>A &lt; B<br>Second</div>"
