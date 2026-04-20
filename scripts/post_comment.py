"""
post_comment.py — run by GitHub Actions after each publish slot.
Reads output/pending_comments.json, posts comment on any video that is now
live, then removes it from the pending list.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

PENDING_PATH = Path(__file__).parent.parent / "output" / "pending_comments.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def build_credentials() -> Credentials:
    # GitHub Actions passes these as env vars from secrets
    client_id = os.environ["YOUTUBE_CLIENT_ID"]
    client_secret = os.environ["YOUTUBE_CLIENT_SECRET"]
    refresh_token = os.environ["YOUTUBE_REFRESH_TOKEN"]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def is_video_public(youtube, video_id: str) -> bool:
    resp = youtube.videos().list(part="status", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return False
    status = items[0]["status"]["privacyStatus"]
    return status == "public"


def post_comment(youtube, video_id: str, text: str) -> None:
    youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
    ).execute()


def main():
    if not PENDING_PATH.exists():
        print("No pending_comments.json — nothing to do.")
        return

    pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    if not pending:
        print("Pending list is empty.")
        return

    creds = build_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    now = datetime.now(timezone.utc)
    remaining = []
    posted = 0
    skipped = 0

    for entry in pending:
        video_id = entry["video_id"]
        comment = entry["comment"]
        publish_at = entry.get("publish_at", "")

        # Skip if publish time hasn't passed yet (with 5-min buffer)
        if publish_at:
            pub_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            if now < pub_dt:
                print(f"[skip] {video_id} — not yet published (scheduled {publish_at})")
                remaining.append(entry)
                skipped += 1
                continue

        try:
            if not is_video_public(youtube, video_id):
                print(f"[skip] {video_id} — not yet public, will retry next run")
                remaining.append(entry)
                skipped += 1
                continue

            post_comment(youtube, video_id, comment)
            print(f"[ok] {video_id} — posted: \"{comment}\"")
            posted += 1

        except HttpError as e:
            print(f"[error] {video_id} — {e}", file=sys.stderr)
            remaining.append(entry)

    PENDING_PATH.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
    print(f"\nDone: {posted} posted, {skipped} skipped, {len(remaining)} remaining.")


if __name__ == "__main__":
    main()
