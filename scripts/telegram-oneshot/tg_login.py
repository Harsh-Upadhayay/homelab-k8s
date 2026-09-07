#!/usr/bin/env python3
"""Generate a Telethon StringSession for the one-shot backfill.

Run this on a trusted machine (your laptop). It logs in AS your Telegram user and
prints a session string — treat that string like a password: it grants full account
access. Store it only in the throwaway Kubernetes Secret the backfill reads (see
README.md), never in git.

    pip install telethon
    python3 tg_login.py

It prompts for api_id + api_hash (from https://my.telegram.org → API development
tools), then your phone number, then the login code Telegram sends to your app, then
your 2FA password if Two-Step Verification is enabled. On success it prints the
StringSession to stdout and nothing else sensitive is written to disk.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("api_id: ").strip())
api_hash = input("api_hash: ").strip()

# .start() drives the whole interactive flow: phone -> code -> 2FA password if set.
with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print(f"\nLogged in as {me.first_name} (@{me.username}, id {me.id})")
    print("\n=== SESSION STRING (secret — full account access) ===\n")
    print(client.session.save())
    print("\nStore this in the tg-oneshot Secret's `session` key. Do not commit it.")
