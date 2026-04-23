"""
Bridge script: uploads rendered video to YouTube using content-pipeline uploader.
"""

import sys
import os
import json
import argparse

from dotenv import load_dotenv

load_dotenv()

# Import from content-pipeline
CONTENT_PIPELINE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "content-pipeline")
sys.path.insert(0, os.path.abspath(CONTENT_PIPELINE_DIR))

from modules.uploader import upload_video  # noqa: E402


def dispatch_comment(video_id: str, comment: str, publish_at: str, manifest_path: str) -> None:
    import urllib.request

    github_token = os.environ.get("GITHUB_PAT")
    github_repo = os.environ.get("GITHUB_REPO")

    if not github_token or not github_repo:
        print("[upload] GITHUB_PAT or GITHUB_REPO not set — skipping comment dispatch")
        return

    payload = json.dumps({
        "event_type": "queue-comment",
        "client_payload": {
            "video_id": video_id,
            "comment": comment,
            "publish_at": publish_at,
            "manifest": os.path.basename(manifest_path),
        },
    }).encode()

    req = urllib.request.Request(
        f"https://api.github.com/repos/{github_repo}/dispatches",
        data=payload,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            print(f"[upload] Comment dispatched to GitHub Actions for {publish_at}")
        else:
            print(f"[upload] Dispatch returned unexpected status {resp.status}")


def post_comment(video_id: str, text: str) -> None:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = os.path.join(CONTENT_PIPELINE_DIR, "token.json")
    if not os.path.exists(token_path):
        print("[upload] No token.json found — skipping comment post")
        return

    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ]
    creds = Credentials.from_authorized_user_file(token_path, scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    youtube = build("youtube", "v3", credentials=creds)
    youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": text}
                },
            }
        },
    ).execute()
    print(f'[upload] Comment posted: "{text}"')


def main():
    parser = argparse.ArgumentParser(description="Upload rendered Short to YouTube")
    parser.add_argument("--manifest", required=True, help="Path to manifest JSON")
    parser.add_argument("--video", required=True, help="Path to rendered MP4")
    args = parser.parse_args()

    manifest_path = os.path.abspath(args.manifest)
    video_path = os.path.abspath(args.video)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    title = manifest.get("title", "YouTube Short")
    description = manifest.get("topic", title)

    print(f"[upload] Uploading: {video_path}")
    print(f"[upload] Title: {title}")

    publish_at = manifest.get("publish_at")

    result = upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=["shorts", "ai", "tech"],
        publish_at=publish_at,
    )

    if not result:
        print("[upload] Upload failed or returned no ID")
        sys.exit(1)

    # upload_video returns a full URL — extract bare ID
    video_id = result.rstrip("/").split("/")[-1]
    result = video_id

    print(f"[upload] Success! Video ID: {result}")

    # Write video_id back to manifest so it's traceable
    manifest["video_id"] = result
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Comment lives in top-level cta block; fall back to cta scene data for older manifests
    comment_text = manifest.get("cta", {}).get("comment")
    if not comment_text:
        cta_scene = next(
            (s for s in manifest.get("scenes", []) if s.get("type") == "cta"),
            None,
        )
        comment_text = cta_scene.get("data", {}).get("comment") if cta_scene else None
    if comment_text and publish_at:
        try:
            dispatch_comment(result, comment_text, publish_at, manifest_path)
        except Exception as e:
            print(f"[upload] Comment dispatch failed (non-fatal): {e}")
    elif comment_text:
        print("[upload] No publish_at — skipping comment scheduling")


if __name__ == "__main__":
    main()
