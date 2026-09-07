#!/usr/bin/env python3
"""List your Telegram groups/channels and their numeric IDs.

The backfill addresses chats by numeric ID (stable; display names are not). Run this
after generating a session to find the IDs for the chats you want to archive, then
put them in config.json's `channels`.

    TG_API_ID=... TG_API_HASH=... TG_SESSION='...' python3 tg_list_chats.py
    # or: python3 tg_list_chats.py path/to/config.json   (reads api_id/api_hash/session)

It also prints each chat's photo+video count so you can gauge how big the job is and
order the config small->large.
"""
import json, os, sys
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Chat, Channel, InputMessagesFilterPhotoVideo

if len(sys.argv) > 1:
    c = json.load(open(sys.argv[1]))
    api_id, api_hash, session = int(c["api_id"]), c["api_hash"], c["session"]
else:
    api_id, api_hash, session = int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"], os.environ["TG_SESSION"]

client = TelegramClient(StringSession(session), api_id, api_hash); client.connect()
print(f"{'id':>14}  {'kind':10} {'photos+vids':>11}  title")
for d in client.iter_dialogs():
    e = d.entity
    if not isinstance(e, (Chat, Channel)):
        continue
    kind = "megagroup" if getattr(e, "megagroup", False) else ("channel" if isinstance(e, Channel) else "group")
    try:
        pv = client.get_messages(e, limit=0, filter=InputMessagesFilterPhotoVideo).total
    except Exception:
        pv = "?"
    print(f"{e.id:>14}  {kind:10} {str(pv):>11}  {d.name}")
client.disconnect()
