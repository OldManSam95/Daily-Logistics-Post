"""Read the private tracker, validate it, and preview or post a fresh Sitrep."""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
ORDERS = {
    "MPF": ["Small Arms", "Heavy Arms", "Heavy Ammunition", "Resources", "Uniforms", "Vehicles", "Structures"],
    "Factory": ["Small Arms", "Heavy Arms", "Heavy Ammunition", "Utility", "Medical", "Resources", "Uniforms"],
}
HEADERS = {
    "MPF": "## 🏭 __**MPF TO DO LIST**__ 🏭",
    "Factory": "## 🔧 __**FACTORY TO DO LIST**__ 🔧",
    "Refinery": "## ⛏️ __**REFINERY TO DO LIST**__ ⛏️",
}
REQUIRED = {"Item", "Category", "Faction", "Unlocked", "Production Method", "Include in Sitrep", "Primary Sitrep Location", "Secondary Sitrep Location"}


class SitrepError(Exception):
    pass


def clean(value):
    return "" if value is None else str(value).strip()


def unique(values):
    result, seen = [], set()
    for value in values:
        value = clean(value)
        if value and value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return result


def parse_start(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return date(1899, 12, 30) + timedelta(days=int(value))
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(clean(value), fmt).date()
        except ValueError:
            pass
    raise SitrepError("War Day 1 Date must be a Sheets date or a date such as 25 Aug 2026 / 2026-08-25.")


def generate(tech, config_rows, today):
    if not tech or not config_rows:
        raise SitrepError("Tech Status or Sitrep Config is empty.")
    headers = [clean(cell) for cell in tech[0]]
    if not REQUIRED.issubset(headers) or len([h for h in headers if h]) != len(set(h for h in headers if h)):
        raise SitrepError("Tech Status has missing or duplicate column headers.")
    if [clean(c) for c in config_rows[0][:2]] != ["Setting", "Value"]:
        raise SitrepError("Sitrep Config must begin with Setting and Value headers.")
    config = {}
    for row in config_rows[1:]:
        if not row or not clean(row[0]):
            continue
        key = clean(row[0])
        if key in config:
            raise SitrepError("Sitrep Config contains a duplicate setting.")
        config[key] = row[1] if len(row) > 1 else ""
    for key in ("Faction", "War Day 1 Date", "Timezone", "MPF Queue Link"):
        if not clean(config.get(key)):
            raise SitrepError(f"Sitrep Config is missing {key}.")
    if clean(config["Timezone"]) != "Europe/London":
        raise SitrepError("Timezone must remain Europe/London to match the workflow schedule.")
    day = (today - parse_start(config["War Day 1 Date"])).days + 1
    if day < 1:
        raise SitrepError("War Day 1 Date is in the future.")
    queue = clean(config["MPF Queue Link"])
    if not re.fullmatch(r"https://discord\.com/channels/\d+/\d+(?:/\d+)?", queue):
        raise SitrepError("MPF Queue Link must be a Discord channel or message link.")
    sections = {name: [] for name in HEADERS}
    for row_number, row in enumerate(tech[1:], 2):
        item = {h: clean(row[i]) if i < len(row) else "" for i, h in enumerate(headers) if h}
        if item["Faction"].casefold() != clean(config["Faction"]).casefold():
            continue
        if item["Unlocked"].casefold() != "yes" or item["Include in Sitrep"].casefold() != "yes":
            continue
        if not item["Item"] or any("\n" in v or "\r" in v or v.startswith(("#REF!", "#VALUE!", "#N/A", "#ERROR!")) for v in item.values()):
            raise SitrepError(f"Invalid included item at Tech Status row {row_number}.")
        method = re.sub(r"\s+", "", item["Production Method"]).casefold()
        destinations = {"mpf": ["MPF"], "factory": ["Factory"], "factory/mpf": ["MPF", "Factory"], "refinery": ["Refinery"]}.get(method)
        if not destinations:
            raise SitrepError(f"Unsupported production method at Tech Status row {row_number}.")
        if item["Category"] == "Resource":
            item["Category"] = "Resources"
        for destination in destinations:
            if destination in ORDERS and item["Category"] not in ORDERS[destination]:
                raise SitrepError(f"Unsupported category for {destination} at Tech Status row {row_number}.")
            sections[destination].append(item)
    if not any(sections.values()):
        raise SitrepError("No eligible items; refusing to send an empty Sitrep.")
    blocks = [f"# Daily Logistics Sitrep - Day {day}"]
    for facility, items in sections.items():
        if not items:
            continue
        locations = unique(loc for item in items for key in ("Primary Sitrep Location", "Secondary Sitrep Location") for loc in item[key].split(","))
        if not locations:
            locations = unique(clean(config.get(f"{facility} Locations")).split(","))
        if not locations or any("\n" in loc or "\r" in loc for loc in locations):
            raise SitrepError(f"Missing or invalid locations for {facility}.")
        lines = [HEADERS[facility] + "\n**Locations:** " + ", ".join(locations)]
        if facility == "Refinery":
            lines.append(", ".join(sorted(unique(item["Item"] for item in items), key=str.casefold)))
        else:
            for category in ORDERS[facility]:
                names = sorted(unique(item["Item"] for item in items if item["Category"] == category), key=str.casefold)
                if names:
                    lines.append(f"**{category}:** " + ", ".join(names))
        if facility == "MPF":
            lines.append(f"*Post Your MPF Queues Here: {queue}*")
        blocks.append("\n\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def discord_length(text):
    # Conservative for Discord clients that count emoji as two UTF-16 units.
    return len(text.encode("utf-16-le")) // 2


def split_message(message, limit=2000):
    chunks = []
    remaining = message.strip()
    while discord_length(remaining) > limit:
        end, units = 0, 0
        for character in remaining:
            units += discord_length(character)
            if units > limit:
                break
            end += 1
        split = remaining.rfind("\n\n", 0, end + 1)
        if split <= 0:
            split = remaining.rfind("\n", 0, end + 1)
        if split <= 0:
            split = remaining.rfind(", ", 0, end)
            if split > 0:
                split += 1
        if split <= 0:
            split = end
        chunks.append(remaining[:split].strip())
        remaining = remaining[split:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def read_sheet():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sheet_id):
        raise SitrepError("GOOGLE_SHEET_ID must contain the ID only, not a spreadsheet URL.")
    try:
        info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
        credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        with AuthorizedSession(credentials) as session:
            response = session.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchGet",
                params=[("ranges", "'Tech Status'"), ("ranges", "'Sitrep Config'"), ("ranges", "'Daily Priorities'"), ("valueRenderOption", "UNFORMATTED_VALUE"), ("dateTimeRenderOption", "SERIAL_NUMBER")],
                timeout=45,
            )
            if response.status_code != 200:
                raise SitrepError(f"Google Sheets returned HTTP {response.status_code}. Check the API is enabled, Sheet ID, and Viewer sharing with the service account.")
            ranges = response.json().get("valueRanges", [])
            if len(ranges) != 3:
                raise SitrepError("Google Sheets did not return all three requested tabs.")
            return ranges[0].get("values", []), ranges[1].get("values", [])
    except SitrepError:
        raise
    except Exception:
        # Credentials, tokens, API response bodies and request URLs never enter logs.
        raise SitrepError("Could not authenticate or read Google Sheets. Check GOOGLE_SERVICE_ACCOUNT_JSON and the service-account setup.") from None


def send(chunks):
    import requests

    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "discord.com" or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+", parts.path):
        raise SitrepError("DAILY_LOGI_POST must contain a valid discord.com webhook URL.")
    query = dict(parse_qsl(parts.query))
    query["wait"] = "true"
    url = urlunsplit(parts._replace(query=urlencode(query), fragment=""))
    for index, chunk in enumerate(chunks, 1):
        for attempt in range(3):
            try:
                response = requests.post(url, json={"content": chunk, "allowed_mentions": {"parse": []}}, timeout=30, allow_redirects=False)
            except requests.RequestException:
                raise SitrepError(f"Discord request failed at part {index}/{len(chunks)}. Delivery is uncertain; inspect Discord before retrying.") from None
            if response.status_code == 429 and attempt < 2:
                try:
                    delay = float(response.json()["retry_after"])
                except (ValueError, KeyError, TypeError):
                    raise SitrepError("Discord rate-limit response could not be read.") from None
                if not 0 <= delay <= 30:
                    raise SitrepError("Discord requested a longer retry delay; posting stopped.")
                time.sleep(delay + 0.5)
                continue
            if response.status_code != 200:
                raise SitrepError(f"Discord returned HTTP {response.status_code} at part {index}/{len(chunks)}. Inspect Discord before retrying; earlier parts may have been sent.")
            print(f"Discord confirmed part {index}/{len(chunks)}.")
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Post to Discord; default is preview only.")
    args = parser.parse_args()
    if args.send and int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
        raise SitrepError("Posting is blocked on reruns to avoid duplicates. Inspect Discord, then start a new manual run if needed.")
    today = datetime.now(LONDON).date()
    tech, config = read_sheet()
    message = generate(tech, config, today)
    chunks = split_message(message)
    Path("sitrep-preview.txt").write_text(message, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(f"Generated from live Google Sheets for {today.isoformat()} (Europe/London).\n\n{len(chunks)} Discord message(s).\n\n" + message)
    print(f"Fresh Sitrep generated for {today.isoformat()}: {len(chunks)} message(s).")
    if args.send:
        if datetime.now(LONDON).date() != today:
            raise SitrepError("The London date changed while generating; refusing to send.")
        send(chunks)
    else:
        print("Preview only. Nothing sent to Discord. Open the run summary or sitrep-preview artifact.")


if __name__ == "__main__":
    try:
        main()
    except SitrepError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("ERROR: Unexpected failure; posting stopped. No old message will be used.", file=sys.stderr)
        sys.exit(1)
