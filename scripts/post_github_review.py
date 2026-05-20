from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def build_review_payload(comments: list[dict[str, Any]], commit_id: str = "", limit: int = 50) -> dict[str, Any]:
    review_comments = []
    for item in comments[:limit]:
        path = item.get("path")
        line = item.get("line")
        body = item.get("body")
        if not path or not line or not body:
            continue
        review_comments.append(
            {
                "path": path,
                "line": int(line),
                "side": item.get("side") or "RIGHT",
                "body": body,
            }
        )
    payload: dict[str, Any] = {
        "event": "COMMENT",
        "body": "Multi-Agent Code Review Lab findings",
        "comments": review_comments,
    }
    if commit_id:
        payload["commit_id"] = commit_id
    return payload


def post_review(repo: str, pull_number: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/pulls/{pull_number}/reviews"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Post MACR review-diff comments to a GitHub pull request.")
    parser.add_argument("--comments", required=True, type=Path, help="GitHub comments JSON produced by review-diff.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pull-number", default=os.getenv("PR_NUMBER", ""))
    parser.add_argument("--commit", default=os.getenv("GITHUB_SHA", ""))
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    comments = json.loads(args.comments.read_text())
    payload = build_review_payload(comments, commit_id=args.commit)
    if not payload["comments"]:
        print("No review comments to post.")
        return
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    missing = [name for name, value in [("repo", args.repo), ("pull-number", args.pull_number), ("token", args.token)] if not value]
    if missing:
        raise SystemExit(f"Missing required GitHub settings: {', '.join(missing)}")
    result = post_review(args.repo, args.pull_number, args.token, payload)
    print(json.dumps({"id": result.get("id"), "html_url": result.get("html_url")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        sys.stderr.write(exc.read().decode("utf-8", errors="replace"))
        raise
