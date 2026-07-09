#!/usr/bin/env python3
"""Create password-hashed users for the NCE Solar Dashboard."""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from solar_live_app import hash_password


USERS_FILE = Path("solar_users.json")


def load_payload() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {"users": []}


def save_payload(payload: dict) -> None:
    USERS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    USERS_FILE.chmod(0o600)


def upsert_user(username: str, role: str, plants: list[str]) -> None:
    payload = load_payload()
    password = getpass.getpass(f"Password for {username}: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    users = payload.setdefault("users", [])
    entry = {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "plants": plants if role != "admin" else ["*"],
    }
    for index, user in enumerate(users):
        if user.get("username") == username:
            users[index] = entry
            break
    else:
        users.append(entry)
    save_payload(payload)
    print(f"Saved {username} to {USERS_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update NCE Solar Dashboard users.")
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "customer"], default="customer")
    parser.add_argument(
        "--plant",
        action="append",
        default=[],
        help='Allowed plant key, for example "Solis::ELVIS GOMES". Repeat for multiple plants.',
    )
    args = parser.parse_args()
    if args.role == "customer" and not args.plant:
        raise SystemExit("Customer users need at least one --plant entry.")
    upsert_user(args.username, args.role, args.plant)


if __name__ == "__main__":
    main()
