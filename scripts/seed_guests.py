#!/usr/bin/env python3
"""Upsert the wedding guest list from CSV into Supabase.

CSV columns: name, phone, num_allowed, guests
  - phone:       one or more human-readable phones, ';'-separated; each
                 normalized to E.164. The first is the primary phone (PK in
                 guest_list); the rest are uploaded as aliases that resolve
                 to the same party at lookup time.
  - num_allowed: positive int; defaults to len(guests) when names are listed, else 8
                 when the guests field is empty (open household cap)
  - guests:      ';'-separated names (CSV is comma-delimited, hence ';')
  - name:        optional; used only for log labels
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from _phone import normalize_phone

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GUESTS = SCRIPT_DIR / "guests.csv"
REQUIRED_COLUMNS = {"phone", "num_allowed", "guests"}

DEFAULT_OPEN_PARTY_CAP = 8


def parse_guests(field: str) -> list[str]:
    return [g.strip() for g in (field or "").split(";") if g.strip()]


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            sys.exit(
                f"error: {path} must have columns {sorted(REQUIRED_COLUMNS)} "
                f"(found {reader.fieldnames})"
            )
        rows: list[dict] = []
        for i, row in enumerate(reader, start=2):
            raw_phone_field = (row.get("phone") or "").strip()
            if not raw_phone_field:
                continue
            raw_phones = [p.strip() for p in raw_phone_field.split(";") if p.strip()]
            phones: list[str] = []
            for raw in raw_phones:
                normalized = normalize_phone(raw)
                if normalized is None:
                    print(f"  skip phone in row {i}: cannot parse {raw!r}")
                    continue
                if normalized in phones:
                    continue
                phones.append(normalized)
            if not phones:
                print(f"  skip row {i}: no valid phones in {raw_phone_field!r}")
                continue
            phone, aliases = phones[0], phones[1:]

            guests = parse_guests(row.get("guests") or "")
            raw_allowed = (row.get("num_allowed") or "").strip()
            try:
                if not guests:
                    try:
                        n = int(raw_allowed) if raw_allowed else None
                    except ValueError:
                        n = None
                    if n is None or n == 1:
                        num_allowed = DEFAULT_OPEN_PARTY_CAP
                    else:
                        num_allowed = n
                else:
                    num_allowed = int(raw_allowed) if raw_allowed else len(guests)
            except ValueError:
                print(f"  skip row {i}: num_allowed {raw_allowed!r} not an int")
                continue
            if num_allowed < 1:
                print(f"  skip row {i}: num_allowed must be >= 1")
                continue

            rows.append({
                "phone": phone,
                "aliases": aliases,
                "guests": guests,
                "num_allowed": num_allowed,
                "_label": (row.get("name") or "").strip(),
            })
    return rows


def _post(url: str, key: str, path: str, on_conflict: str, payload: list[dict]) -> None:
    resp = requests.post(
        f"{url}/rest/v1/{path}",
        params={"on_conflict": on_conflict},
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"error: Supabase {path} returned {resp.status_code}: {resp.text}")


def upsert(url: str, key: str, rows: list[dict]) -> None:
    primary_payload = [{
        "phone": r["phone"],
        "guests": r["guests"],
        "num_allowed": r["num_allowed"],
    } for r in rows]
    _post(url, key, "guest_list", "phone", primary_payload)

    alias_payload = [
        {"alias_phone": alias, "primary_phone": r["phone"]}
        for r in rows
        for alias in r.get("aliases", [])
    ]
    if alias_payload:
        _post(url, key, "phone_aliases", "alias_phone", alias_payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="parse CSV, print summary, do not call Supabase")
    parser.add_argument("--guests", type=Path, default=DEFAULT_GUESTS, help=f"path to guest CSV (default: {DEFAULT_GUESTS})")
    args = parser.parse_args()

    load_dotenv(SCRIPT_DIR.parent / ".env")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not args.guests.exists():
        sys.exit(f"error: guest file not found: {args.guests} (copy guests.csv.example to guests.csv)")

    rows = load_rows(args.guests)
    print(f"Guest file:  {args.guests}")
    print(f"Parsed rows: {len(rows)}")
    for r in rows:
        label = f" ({r['_label']})" if r["_label"] else ""
        alias_str = f"  aliases={r['aliases']}" if r["aliases"] else ""
        print(f"  {r['phone']}{label}{alias_str}  allowed={r['num_allowed']}  guests={r['guests']}")

    if not rows:
        print("Nothing to upload.")
        return 0

    if args.dry_run:
        print("\nDRY RUN — not calling Supabase.")
        return 0

    if not url or not key:
        sys.exit("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")

    answer = input(f"\nUpsert {len(rows)} rows into {url}? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return 1

    upsert(url, key, rows)
    print(f"Done. Upserted {len(rows)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
