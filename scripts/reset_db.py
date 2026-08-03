#!/usr/bin/env python3
"""Flush RSVP data from Supabase without dropping tables.

Default: deletes every row from `rsvps`, leaves `guest_list` alone.
--all:   also deletes every row from `guest_list` (FK cascade re-clears `rsvps`).

Uses the service-role / secret key, which bypasses RLS.
"""

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent


def delete_all(url: str, key: str, table: str, pk: str) -> int:
    # PostgREST requires a filter on DELETE; `<pk>=not.is.null` matches every row
    # since primary keys are non-null. `return=representation` lets us count.
    resp = requests.delete(
        f"{url}/rest/v1/{table}",
        params={pk: "not.is.null"},
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "return=representation",
        },
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"error: Supabase returned {resp.status_code} on {table}: {resp.text}")
    try:
        return len(resp.json())
    except ValueError:
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="also clear guest_list (cascades through to rsvps)",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    load_dotenv(SCRIPT_DIR.parent / ".env")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        sys.exit("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")

    targets = ["rsvps"] + (["guest_list"] if args.all else [])
    print(f"Project:    {url}")
    print(f"Will clear: {', '.join(targets)}")

    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 1

    deleted_rsvps = delete_all(url, key, "rsvps", "id")
    print(f"  rsvps:      deleted {deleted_rsvps} rows")
    if args.all:
        deleted_guests = delete_all(url, key, "guest_list", "phone")
        print(f"  guest_list: deleted {deleted_guests} rows")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
