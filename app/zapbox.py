"""Zapbox API transport for the local Quickly pilot."""

import os
import subprocess
from email.utils import formataddr

import httpx

BASE_URL = "https://api.zapmail.ai/api/v2/onebox"


class ZapboxError(Exception):
    """Zapbox refused a request before accepting a send."""


class ZapboxAmbiguousSend(Exception):
    """A send may have reached Zapbox and must be reconciled before retry."""


def _credentials() -> dict[str, str]:
    key = os.getenv("ZAPBOX_API_KEY", "").strip()
    if not key:
        account = os.getenv("ZAPBOX_KEYCHAIN_ACCOUNT", "").strip()
        if account:
            try:
                key = subprocess.check_output(
                    ["/usr/bin/security", "find-generic-password", "-a", account,
                     "-s", "enlyte-zapbox-api", "-w"],
                    text=True, stderr=subprocess.DEVNULL,
                ).strip()
            except (OSError, subprocess.CalledProcessError):
                pass
    workspace = os.getenv("ZAPBOX_WORKSPACE_ID", "").strip()
    if not key or not workspace:
        raise ZapboxError("Zapbox API key and workspace ID are required")
    return {"x-auth-zapmail": key, "x-workspace-id": workspace}


def _allow_send(sender: str, recipient: str) -> None:
    allowed_sender = os.getenv("ZAPBOX_PILOT_SENDER", "").strip().casefold()
    allowed_recipients = {
        address.strip().casefold()
        for address in os.getenv("ZAPBOX_PILOT_RECIPIENTS", "").split(",")
        if address.strip()
    }
    if not allowed_sender or sender.casefold() != allowed_sender or recipient.casefold() not in allowed_recipients:
        raise ZapboxError("Send blocked by Zapbox pilot sender and recipient allowlist")


def send_message(*, account: str, to: str, subject: str, body: str, from_name: str = "",
                 thread_id: str | None = None, message_id: str | None = None) -> dict:
    _allow_send(account, to)
    if bool(thread_id) != bool(message_id):
        raise ZapboxError("Threaded send requires both threadId and messageId")
    payload = {
        "account": formataddr((from_name, account)) if from_name else account,
        "to": to, "subject": subject, "body": body,
    }
    if thread_id:
        payload.update(threadId=thread_id, messageId=message_id)
    try:
        with httpx.Client(follow_redirects=False, timeout=45) as client:
            response = client.post(f"{BASE_URL}/send-email", headers=_credentials(), json=payload)
    except httpx.RequestError as exc:
        raise ZapboxAmbiguousSend("Zapbox send outcome unknown after network error") from exc
    if 400 <= response.status_code < 500:
        raise ZapboxError(f"Zapbox rejected send with HTTP {response.status_code}")
    if response.status_code >= 500:
        raise ZapboxAmbiguousSend(f"Zapbox send outcome unknown after HTTP {response.status_code}")
    try:
        parsed = response.json()
        data = parsed["data"]
        if not parsed["success"] or not data["messageId"] or not data["threadId"]:
            raise ValueError("Incomplete Zapbox send response")
    except (ValueError, KeyError, TypeError) as exc:
        raise ZapboxAmbiguousSend("Zapbox send outcome unknown after invalid response") from exc
    return data


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(follow_redirects=False, timeout=45) as client:
        response = await client.get(f"{BASE_URL}/{path}", headers=_credentials(), params=params)
        response.raise_for_status()
        parsed = response.json()
    if not parsed.get("success") or not isinstance(parsed.get("data"), list):
        raise ZapboxError("Zapbox returned invalid list response")
    return parsed


async def list_accounts() -> list[dict]:
    accounts = []
    page = 1
    while True:
        result = await _get("connected-accounts", {"page": page, "limit": 100})
        previous_ids = {item.get("id") for item in accounts}
        if page > 1 and any(item.get("id") in previous_ids for item in result["data"]):
            raise ZapboxError("Zapbox account pagination repeated a page")
        accounts.extend(result["data"])
        pagination = result.get("pagination") or {}
        if not pagination.get("hasNextPage"):
            return accounts
        if page >= int(pagination.get("totalPages") or page):
            raise ZapboxError("Zapbox account pagination is incomplete")
        page += 1


async def fetch_messages(account: str, folder: str = "INBOX", cursor: str | None = None,
                         limit: int = 100) -> dict:
    params = {"account": account, "folder": folder, "limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return await _get("top-emails", params)


async def get_thread(account: str, thread_id: str) -> list[dict]:
    result = await _get("thread-emails", {"threadId": thread_id})
    return [message for message in result["data"] if
            message.get("account", account).casefold() == account.casefold()]
