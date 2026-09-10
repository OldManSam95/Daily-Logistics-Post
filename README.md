# Daily Discord Logistics Sitrep

GitHub Actions reads the private Google Sheet and generates a fresh Sitrep in the same process that posts it. No daily repository commit or ChatGPT task is needed. The legacy `sitrep-message.txt` is not read.

## Repository secrets

- `GOOGLE_SERVICE_ACCOUNT_JSON`: complete service-account JSON key. Enable Google Sheets API in its project and share the tracker with its `client_email` as Viewer.
- `GOOGLE_SHEET_ID`: spreadsheet ID only.
- `DAILY_LOGI_POST`: existing Discord webhook URL.

Never commit credentials. The script uses only the Sheets read-only OAuth scope. GitHub repository permissions are read-only.

## First preview and activation

1. Confirm these files exist on `main`: `scripts/sitrep.py`, `requirements.txt`, `tests/test_sitrep.py`, and `.github/workflows/discord-daily.yml`.
2. In Actions, open **Daily Discord Logistics Sitrep** and enable the workflow if disabled. Enabling it also enables the 17:30 London schedule.
3. Select **Run workflow**, branch `main`, leave **Send to Discord now** unchecked, and run.
4. Check the run is green. Its Summary and `sitrep-preview` artifact contain the generated message. A successful preview confirms the Google credentials and Sheet access, but does not test webhook delivery.
5. If preview fails, disable the workflow while investigating. A failed fetch or validation never falls back to yesterday's message.
6. Leave the workflow enabled after a successful preview for daily posting. Keep the old ChatGPT refresh disabled.

Scheduled runs generate and send at 17:30 Europe/London, including daylight-saving changes. GitHub may start scheduled jobs late. Commits do not trigger a post. Manual runs default to preview; check **Send to Discord now** only when an immediate post is intended.

## Formatting and validation

Tech Status is read by header name. Items require the configured faction, Unlocked = Yes, and Include in Sitrep = Yes. Production methods are Factory, MPF, Factory / MPF, and Refinery.

MPF category order: Small Arms, Heavy Arms, Heavy Ammunition, Resources, Uniforms, Vehicles, Structures.

Factory category order: Small Arms, Heavy Arms, Heavy Ammunition, Utility, Medical, Resources, Uniforms.

Resource is displayed as Resources. Empty categories and empty facility sections are omitted. Items are alphabetized within categories. Per-item locations are split on commas and deduplicated in first-seen order, with the facility configuration as a fallback. Refinery has one item list without categories. The MPF queue link is preserved. Daily Priorities is fetched, but priority/note/transport sections remain omitted as requested.

The current war day is calculated from War Day 1 Date and the current London date, not the potentially cached Current War Day formula. Actual Sheets dates and text dates like `25 Aug 2026` or `2026-08-25` are supported.

Unknown included production methods/categories, missing required headers/settings, unreadable tabs and an entirely empty selection stop posting. The script creates `sitrep-preview.txt` from the live read; it never uses a stored message as input.

## Delivery and troubleshooting

Long posts split at paragraph/line/item boundaries into messages of at most 2,000 UTF-16 units. Extremely long individual strings are split as a last resort. Mentions are disabled. Discord must confirm each message; short rate-limit responses are retried. Other delivery failures stop without automatic retries because delivery may be uncertain.

Workflow reruns cannot send again. A newly dispatched manual send is allowed and can duplicate a previous post: inspect Discord before using it, particularly after a partial multi-message failure.

- Google 403: check the Sheets API is enabled and Viewer sharing uses the service account's email.
- Google 404: check the Sheet ID and sharing.
- Authentication failure: verify the complete JSON key was placed in the secret.
- Discord 401/403/404: verify the existing webhook is still valid.
- Validation failure: correct the indicated Sheet setting/header/row, then run another preview.

Local tests (no secrets or network required): `python -m unittest discover -s tests -v`.
