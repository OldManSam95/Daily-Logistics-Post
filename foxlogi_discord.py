#!/usr/bin/env python3
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FOXLOGI_BASE = "https://foxlogi.com"
PLANNER_PATH = "/api/logistic/planner/"
STATE_FILE = Path(os.getenv("FOXLOGI_MESSAGE_STATE", "foxlogi-message-state.json"))
DISCORD_CHUNK_LIMIT = 1850

FOXLOGI_API_KEY = os.environ["FOXLOGI_API_KEY"].strip()
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"].strip()

UA = "NOBLE-Foxlogi-Discord/1.0"


def request_json(url, *, method="GET", data=None, headers=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req_headers = {"Accept": "application/json", "User-Agent": UA}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from e


def foxlogi_get(path):
    return request_json(
        FOXLOGI_BASE + path,
        headers={"Authorization": f"Bearer {FOXLOGI_API_KEY}"},
    )


def webhook_base():
    parts = urllib.parse.urlsplit(DISCORD_WEBHOOK_URL)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def webhook_post(content):
    url = webhook_base() + "?wait=true"
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    data = request_json(url, method="POST", data=payload)
    if not isinstance(data, dict) or "id" not in data:
        raise RuntimeError("Discord webhook did not return a message id")
    return str(data["id"])


def webhook_edit(message_id, content):
    url = f"{webhook_base()}/messages/{message_id}"
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    request_json(url, method="PATCH", data=payload)


def webhook_delete(message_id):
    url = f"{webhook_base()}/messages/{message_id}"
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30):
            return
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"DELETE {url} -> HTTP {e.code}: {detail}") from e


def safe_name(record, fallback):
    if isinstance(record, dict):
        return str(record.get("name") or record.get("title") or fallback)
    return fallback


def normalize_items(planner):
    items = planner.get("items") or {}
    return items if isinstance(items, dict) else {}


def item_name(items, item_id, override=None):
    if override:
        return str(override)
    rec = items.get(str(item_id)) or items.get(item_id)
    if isinstance(rec, dict):
        return str(rec.get("name") or rec.get("title") or rec.get("code_name") or f"Item {item_id}")
    return f"Item {item_id}"


def item_category(items, item_id, override=None):
    if override:
        return str(override)
    rec = items.get(str(item_id)) or items.get(item_id)
    if isinstance(rec, dict):
        return str(rec.get("category") or "")
    return ""


def location_name(locations, location_id):
    rec = locations.get(str(location_id)) or locations.get(location_id)
    return safe_name(rec, f"Location {location_id}")


def intish(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


FACTORY_ORDER = {
    "small arms": 0,
    "smallarms": 0,
    "heavy arms": 1,
    "heavyarms": 1,
    "heavy ammunition": 2,
    "heavy ammo": 2,
    "heavyammo": 2,
    "utility": 3,
    "medical": 4,
    "resources": 5,
    "supplies": 5,
    "uniforms": 6,
}

MPF_ORDER = {
    "small arms": 0,
    "smallarms": 0,
    "heavy arms": 1,
    "heavyarms": 1,
    "heavy ammunition": 2,
    "heavy ammo": 2,
    "heavyammo": 2,
    "resources": 3,
    "supplies": 3,
    "uniforms": 4,
    "vehicles": 5,
    "cratedvehicles": 5,
    "structures": 6,
    "cratedstructures": 6,
}


def sort_items(entries, order_map):
    def rank(entry):
        category = (entry.get("category") or "").strip().lower()
        return (order_map.get(category, 999), category, entry.get("name", "").lower())
    return sorted(entries, key=rank)


def lines_transport(planner, locations, items):
    group = planner.get("transport") or {}
    out = []
    if not isinstance(group, dict):
        return out
    for dest_id, source_blocks in group.items():
        if not isinstance(source_blocks, dict):
            continue
        dest = location_name(locations, dest_id)
        for src_id, groups in source_blocks.items():
            if not isinstance(groups, dict):
                continue
            manifest = {}
            for grp in groups.values():
                if not isinstance(grp, dict):
                    continue
                for item_id, qty in (grp.get("items") or {}).items():
                    manifest[str(item_id)] = manifest.get(str(item_id), 0) + intish(qty)
            if not manifest:
                continue
            total = sum(manifest.values())
            out.append(f"**{location_name(locations, src_id)} → {dest}** — {total:,} crates")
            entries = [
                {"name": item_name(items, item_id), "qty": qty, "category": item_category(items, item_id)}
                for item_id, qty in manifest.items()
                if qty > 0
            ]
            for entry in sort_items(entries, FACTORY_ORDER):
                out.append(f"• {entry['qty']:,}× {entry['name']}")
    return out


def lines_craft(planner, locations, items):
    group = planner.get("craft") or {}
    out = []
    if not isinstance(group, dict):
        return out
    for loc_id, payload in group.items():
        if not isinstance(payload, dict):
            continue
        req = payload.get("items") or {}
        if not isinstance(req, dict) or not req:
            continue
        out.append(f"**{location_name(locations, loc_id)}**")
        entries = [
            {"name": item_name(items, item_id), "qty": intish(qty), "category": item_category(items, item_id)}
            for item_id, qty in req.items()
            if intish(qty) > 0
        ]
        for entry in sort_items(entries, FACTORY_ORDER):
            out.append(f"• {entry['qty']:,} crates — {entry['name']}")
    return out


def lines_refinery(planner, locations, items):
    group = planner.get("resource") or {}
    out = []
    if not isinstance(group, dict):
        return out
    for loc_id, payload in group.items():
        if not isinstance(payload, dict) or not payload:
            continue
        entries = []
        for output_id, detail in payload.items():
            if isinstance(detail, dict):
                crates = intish(detail.get("crates"))
                output_qty = intish(detail.get("output"))
                input_qty = intish(detail.get("input"))
            else:
                crates = intish(detail)
                output_qty = 0
                input_qty = 0
            if crates <= 0 and output_qty <= 0 and input_qty <= 0:
                continue
            entries.append((output_id, crates, output_qty, input_qty))
        if not entries:
            continue
        out.append(f"**{location_name(locations, loc_id)}**")
        for output_id, crates, output_qty, input_qty in entries:
            label = item_name(items, output_id)
            if crates > 0:
                out.append(f"• {crates:,} crates — {label}")
            elif output_qty > 0:
                out.append(f"• {output_qty:,} — {label}")
            else:
                out.append(f"• Raw required: {input_qty:,} — {label}")
    return out


def mpf_details(location_id, fallback_payload, items):
    try:
        calc = foxlogi_get(f"/api/mpf/calculate-orders/{location_id}/")
    except Exception as exc:
        print(f"Warning: MPF calculation failed for {location_id}: {exc}")
        calc = None

    entries = []
    if isinstance(calc, dict):
        requests = calc.get("total_request") or {}
        meta = calc.get("items") or {}
        if isinstance(requests, dict):
            for item_id, qty in requests.items():
                qty = intish(qty)
                if qty <= 0:
                    continue
                rec = meta.get(str(item_id)) or meta.get(item_id) or {}
                override_name = rec.get("name") if isinstance(rec, dict) else None
                override_cat = rec.get("category") if isinstance(rec, dict) else None
                entries.append({
                    "name": item_name(items, item_id, override_name),
                    "qty": qty,
                    "category": item_category(items, item_id, override_cat),
                })

    if not entries and isinstance(fallback_payload, dict):
        req = fallback_payload.get("items") or fallback_payload.get("total_request") or {}
        if isinstance(req, dict):
            for item_id, qty in req.items():
                qty = intish(qty)
                if qty > 0:
                    entries.append({
                        "name": item_name(items, item_id),
                        "qty": qty,
                        "category": item_category(items, item_id),
                    })

    return sort_items(entries, MPF_ORDER)


def lines_mpf(planner, locations, items):
    group = planner.get("mpf") or {}
    out = []
    if not isinstance(group, dict):
        return out
    for loc_id, payload in group.items():
        entries = mpf_details(loc_id, payload, items)
        if not entries:
            out.append(f"**{location_name(locations, loc_id)}** — MPF work required")
            continue
        out.append(f"**{location_name(locations, loc_id)}**")
        for entry in entries:
            out.append(f"• {entry['qty']:,} crates — {entry['name']}")
    return out


def build_message(planner):
    if not isinstance(planner, dict):
        raise RuntimeError("Foxlogi planner returned an unexpected response")

    locations = planner.get("locations") or {}
    if not isinstance(locations, dict):
        locations = {}
    items = normalize_items(planner)

    sections = [
        ("🚛 TRANSPORT", lines_transport(planner, locations, items)),
        ("🏭 FACTORY", lines_craft(planner, locations, items)),
        ("⚗️ REFINERY", lines_refinery(planner, locations, items)),
        ("🏗️ MPF", lines_mpf(planner, locations, items)),
    ]

    now = int(time.time())
    lines = [
        "# 🚚 NOBLE LIVE LOGISTICS",
        f"*Current Foxlogi tasks • Updated <t:{now}:R>*",
        "",
    ]

    any_tasks = False
    for title, body in sections:
        if not body:
            continue
        any_tasks = True
        lines.extend([f"## {title}", *body, ""])

    if not any_tasks:
        lines.extend(["✅ **No outstanding logistics tasks right now.**", ""])

    lines.append("-# Source: Foxlogi")
    return "\n".join(lines).strip()


def chunk_message(text):
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= DISCORD_CHUNK_LIMIT:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= DISCORD_CHUNK_LIMIT:
            current = line
        else:
            while len(line) > DISCORD_CHUNK_LIMIT:
                chunks.append(line[:DISCORD_CHUNK_LIMIT])
                line = line[DISCORD_CHUNK_LIMIT:]
            current = line
    if current:
        chunks.append(current)

    if len(chunks) > 1:
        total = len(chunks)
        for i in range(1, total):
            prefix = f"# 🚚 NOBLE LIVE LOGISTICS ({i+1}/{total})\n"
            if len(prefix) + len(chunks[i]) <= 2000:
                chunks[i] = prefix + chunks[i]
    return chunks


def load_state():
    if not STATE_FILE.exists():
        return {"message_ids": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        ids = data.get("message_ids") if isinstance(data, dict) else []
        return {"message_ids": [str(x) for x in ids if x]}
    except Exception:
        return {"message_ids": []}


def save_state(message_ids):
    STATE_FILE.write_text(
        json.dumps({"message_ids": message_ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def sync_messages(chunks):
    state = load_state()
    ids = list(state["message_ids"])
    new_ids = []

    for idx, content in enumerate(chunks):
        if idx < len(ids):
            try:
                webhook_edit(ids[idx], content)
                new_ids.append(ids[idx])
                continue
            except RuntimeError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                print(f"Discord message {ids[idx]} no longer exists; recreating")
        new_ids.append(webhook_post(content))

    for stale_id in ids[len(chunks):]:
        webhook_delete(stale_id)

    save_state(new_ids)
    return new_ids


def main():
    planner = foxlogi_get(PLANNER_PATH)
    message = build_message(planner)
    chunks = chunk_message(message)
    ids = sync_messages(chunks)
    print(f"Updated {len(ids)} Discord message(s) from Foxlogi.")


if __name__ == "__main__":
    main()
