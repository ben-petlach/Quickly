# Local Zapbox pilot

Quickly runs its web UI and scheduler on `127.0.0.1:8765`. PostgreSQL stores campaigns, send attempts and replies on the same machine. Keep the host awake during a sequence.

## Start on macOS

Install the Python requirements into `.venv` using Python 3.12, build the React UI in `frontend` with Node 22, and put PostgreSQL client and server tools on PATH. The launcher initializes an isolated database on port 5438.

Keep local settings in a mode-600 file at `.local-pilot/zapbox.env` under a mode-700 directory. Supply `ZAPBOX_WORKSPACE_ID`, `ZAPBOX_PILOT_SENDER`, and a comma-separated `ZAPBOX_PILOT_RECIPIENTS` list. For the existing macOS Keychain entry, set `ZAPBOX_KEYCHAIN_ACCOUNT` to its account name. The lookup uses service `enlyte-zapbox-api`. The provider also accepts a server-side `ZAPBOX_API_KEY`.

From the fork root, run `scripts/local-pilot.sh run`. Open [Quickly](http://127.0.0.1:8765), then select Zapbox in the inbox connection screen. Use the configured sender. The launcher starts in test mode by default on a new database. Check Quickly’s persisted test-mode setting before expecting a live message.

## Run an owned-address test

Create a paused campaign with the connected inbox. Import the owned contacts and their custom fields, preview each lead, then activate. A missing field blocks launch. Edits affecting approved rendered content hold sends until reviewed and relaunched. Use at least one minute between mailbox sends.

Quickly dispatches each due message through Zapbox. The send attempt is recorded before the network call. If an outcome is unknown after a timeout or restart, check Zapbox Sent and the local attempt before taking further action. Preserve the original attempt.

Unibox sync reads known campaign threads and matches inbound messages to the enrolled address. Look for the reply in Unibox, the stopped follow-up in the campaign queue, and the unique reply in campaign stats. Use the recipient mailbox to verify delivery and threading. New-thread responses, bounces and automated replies require separate validation before broader use.
