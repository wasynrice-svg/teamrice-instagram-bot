#!/usr/bin/env python3
"""
Team Rice Instagram bot.

What it does
  post           Publishes any post in posts.csv whose time has arrived.
  reply          Replies to new comments that match a rule in reply_rules.json.
  refresh-token  Renews the Instagram access token so it doesn't expire.
  all            post + reply (this is what the 15-minute schedule runs).

It only uses Python's built-in libraries, so there is nothing to install.

Settings come from environment variables (GitHub Actions sets these for you):
  IG_ACCESS_TOKEN   required - your long-lived Instagram token (a GitHub secret)
  DRY_RUN           "true" = show what would happen without touching Instagram
  GH_PAT            optional - lets refresh-token save the renewed token itself
"""

import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
POSTS_FILE = ROOT / "posts.csv"
RULES_FILE = ROOT / "reply_rules.json"
STATE_DIR = ROOT / "state"
POSTED_STATE = STATE_DIR / "posted.json"
COMMENT_STATE = STATE_DIR / "comments.json"

VIDEO_EXTENSIONS = (".mp4", ".mov")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def is_dry_run():
    return os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def now_utc():
    override = os.environ.get("BOT_NOW")  # only used for testing
    if override:
        return datetime.fromisoformat(override).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def parse_ig_time(value):
    # Instagram returns times like 2026-10-14T15:04:05+0000
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


CONFIG = load_json(CONFIG_FILE, {})
API_VERSION = CONFIG.get("api_version", "v23.0")
API_BASE = os.environ.get("IG_API_BASE", "https://graph.instagram.com").rstrip("/")


class InstagramError(Exception):
    pass


class Instagram:
    def __init__(self, token):
        if not token:
            raise SystemExit(
                "IG_ACCESS_TOKEN is missing. Add it under Settings > Secrets and "
                "variables > Actions in your GitHub repo (see README step 4)."
            )
        self.token = token
        self._user = None

    def _request(self, method, path, params=None, json_body=None, versioned=True):
        params = dict(params or {})
        params["access_token"] = self.token
        prefix = f"{API_BASE}/{API_VERSION}" if versioned else API_BASE
        url = f"{prefix}/{path.lstrip('/')}"
        data = None
        headers = {}
        if method == "GET":
            url += "?" + urllib.parse.urlencode(params)
        elif json_body is not None:
            url += "?" + urllib.parse.urlencode({"access_token": self.token})
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = urllib.parse.urlencode(params).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                err = json.loads(body).get("error", {})
                message = err.get("message", body)
            except ValueError:
                message = body
            raise InstagramError(f"{method} {path} failed ({e.code}): {message}") from None

    def get(self, path, **params):
        return self._request("GET", path, params)

    def post(self, path, **params):
        return self._request("POST", path, params)

    @property
    def user(self):
        if self._user is None:
            self._user = self.get("me", fields="user_id,username")
        return self._user

    @property
    def user_id(self):
        return self.user.get("user_id") or self.user.get("id")

    # ---- publishing -----------------------------------------------------

    def wait_until_ready(self, container_id, max_wait_seconds=300):
        waited = 0
        while True:
            status = self.get(container_id, fields="status_code,status").get("status_code")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram could not process media {container_id} ({status}).")
            if waited >= max_wait_seconds:
                raise InstagramError(f"Media {container_id} still processing after {max_wait_seconds}s.")
            time.sleep(10)
            waited += 10

    def create_container(self, url, caption=None, carousel_item=False):
        params = {}
        if url.lower().split("?")[0].endswith(VIDEO_EXTENSIONS):
            params["media_type"] = "VIDEO" if carousel_item else "REELS"
            params["video_url"] = url
        else:
            params["image_url"] = url
        if carousel_item:
            params["is_carousel_item"] = "true"
        elif caption:
            params["caption"] = caption
        return self.post(f"{self.user_id}/media", **params)["id"]

    def publish(self, media_urls, caption):
        if len(media_urls) == 1:
            container = self.create_container(media_urls[0], caption)
        else:
            children = [self.create_container(u, carousel_item=True) for u in media_urls]
            for child in children:
                self.wait_until_ready(child)
            container = self.post(
                f"{self.user_id}/media",
                media_type="CAROUSEL",
                children=",".join(children),
                caption=caption,
            )["id"]
        self.wait_until_ready(container)
        return self.post(f"{self.user_id}/media_publish", creation_id=container)["id"]

    # ---- comments --------------------------------------------------------

    def recent_media(self, limit):
        return self.get(f"{self.user_id}/media", fields="id,caption,timestamp,permalink", limit=limit).get("data", [])

    def comments(self, media_id):
        return self.get(f"{media_id}/comments", fields="id,text,username,timestamp,from").get("data", [])

    def reply_publicly(self, comment_id, message):
        return self.post(f"{comment_id}/replies", message=message)

    def reply_privately(self, comment_id, message):
        return self._request(
            "POST",
            f"{self.user_id}/messages",
            json_body={"recipient": {"comment_id": comment_id}, "message": {"text": message}},
        )

    # ---- token -----------------------------------------------------------

    def refresh_token(self):
        return self._request(
            "GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, versioned=False
        )


# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------

def media_url(item):
    item = item.strip()
    if item.startswith("http://") or item.startswith("https://"):
        return item
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        raise ValueError(
            f"'{item}' is a file name, but GITHUB_REPOSITORY isn't set. "
            "Use a full https:// link or run this inside GitHub Actions."
        )
    path = urllib.parse.quote(f"images/{item}")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def read_posts():
    if not POSTS_FILE.exists():
        return []
    with open(POSTS_FILE, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    posts = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not row.get("id"):
            continue
        posts.append({**row, "_line": n})
    return posts


def run_post(ig):
    tz = ZoneInfo(CONFIG.get("timezone", "America/Edmonton"))
    grace = timedelta(hours=float(CONFIG.get("late_post_grace_hours", 12)))
    state = load_json(POSTED_STATE, {})
    now = now_utc()
    changed = False

    for post in read_posts():
        pid = post["id"]
        if post.get("status", "").lower() in ("draft", "paused", "skip"):
            continue
        record = state.get(pid, {})
        if record.get("status") in ("published", "too_late") or record.get("attempts", 0) >= 3:
            continue
        try:
            when = datetime.strptime(post["publish_at"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except (KeyError, ValueError):
            log(f"! Row {post['_line']} ({pid}): publish_at must look like 2026-10-20 09:30 - skipping.")
            continue
        if when > now:
            continue
        if now - when > grace:
            log(f"- {pid}: was due {when:%b %d %H:%M} but that's too long ago, not posting late.")
            state[pid] = {"status": "too_late", "due": when.isoformat()}
            changed = True
            continue

        try:
            urls = [media_url(m) for m in post.get("media", "").split("|") if m.strip()]
        except ValueError as e:
            log(f"! {pid}: {e}")
            continue
        if not urls:
            log(f"! {pid}: no media listed - skipping.")
            continue
        if len(urls) > 10:
            log(f"! {pid}: carousels can have at most 10 items - skipping.")
            continue
        caption = post.get("caption", "").replace("\\n", "\n")

        if is_dry_run():
            log(f"[dry run] Would publish {pid} ({len(urls)} item(s)): {caption[:60]!r}")
            continue

        try:
            media_id = ig.publish(urls, caption)
            log(f"+ Published {pid} -> Instagram media {media_id}")
            state[pid] = {"status": "published", "media_id": media_id, "published_at": now.isoformat()}
        except InstagramError as e:
            attempts = record.get("attempts", 0) + 1
            log(f"! {pid}: {e} (attempt {attempts} of 3)")
            state[pid] = {"status": "error", "attempts": attempts, "last_error": str(e)}
        changed = True

    if changed:
        save_json(POSTED_STATE, state)


# --------------------------------------------------------------------------
# Comment replies
# --------------------------------------------------------------------------

def find_rule(text, rules):
    lowered = (text or "").lower()
    for rule in rules:
        for kw in rule.get("keywords", []):
            kw = kw.lower().strip()
            if not kw:
                continue
            # whole-word match for words, simple "contains" for symbols like "$" or "?"
            if re.fullmatch(r"[\w' ]+", kw):
                if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", lowered):
                    return rule
            elif kw in lowered:
                return rule
    return None


def fill(template, comment):
    return template.replace("{username}", comment.get("username", "there"))


def run_reply(ig):
    rules_cfg = load_json(RULES_FILE, {})
    if not rules_cfg.get("enabled", False):
        log("Comment replies are turned off (set \"enabled\": true in reply_rules.json).")
        return
    rules = rules_cfg.get("rules", [])
    settings = rules_cfg.get("settings", {})
    check_posts = int(settings.get("check_last_n_posts", 10))
    max_replies = int(settings.get("max_replies_per_run", 20))

    state = load_json(COMMENT_STATE, {})
    now = now_utc()
    if "since" not in state:
        # First run: only answer comments that arrive from now on, not old ones.
        state = {"since": now.isoformat(), "handled": [], "replied_pairs": []}
        log("First run - comment replies start from now; older comments are left alone.")
        if not is_dry_run():
            save_json(COMMENT_STATE, state)
        return

    since = datetime.fromisoformat(state["since"])
    handled = set(state.get("handled", []))
    replied_pairs = set(state.get("replied_pairs", []))
    my_username = (ig.user.get("username") or "").lower()
    sent = 0

    for media in ig.recent_media(check_posts):
        for c in ig.comments(media["id"]):
            if sent >= max_replies:
                break
            cid = c["id"]
            if cid in handled:
                continue
            if parse_ig_time(c["timestamp"]) < since:
                handled.add(cid)
                continue
            handled.add(cid)
            username = (c.get("username") or "").lower()
            if username == my_username:
                continue
            pair = f"{media['id']}:{username}"
            if pair in replied_pairs:
                continue  # never reply to the same person twice on one post
            rule = find_rule(c.get("text"), rules)
            if not rule:
                continue

            label = rule.get("name", "rule")
            if is_dry_run():
                log(f"[dry run] Would answer @{c.get('username')} on {media['id']} using '{label}': {c.get('text')!r}")
                continue
            try:
                if rule.get("public_reply"):
                    ig.reply_publicly(cid, fill(rule["public_reply"], c))
                if rule.get("private_reply"):
                    ig.reply_privately(cid, fill(rule["private_reply"], c))
                log(f"+ Replied to @{c.get('username')} ('{label}')")
                replied_pairs.add(pair)
                sent += 1
            except InstagramError as e:
                log(f"! Could not reply to comment {cid}: {e}")

    if not is_dry_run():
        state["handled"] = sorted(handled)[-5000:]
        state["replied_pairs"] = sorted(replied_pairs)[-5000:]
        save_json(COMMENT_STATE, state)
    log(f"Comment check done - {sent} repl{'y' if sent == 1 else 'ies'} sent.")


# --------------------------------------------------------------------------
# Token refresh
# --------------------------------------------------------------------------

def run_refresh(ig):
    if is_dry_run():
        log("[dry run] Would refresh the access token.")
        return
    result = ig.refresh_token()
    new_token = result.get("access_token")
    days = int(result.get("expires_in", 0)) // 86400
    if not new_token:
        raise SystemExit(f"Refresh failed: {result}")
    log(f"Token refreshed - good for about {days} more days.")
    if new_token == ig.token:
        return
    if not os.environ.get("GH_PAT"):
        raise SystemExit(
            "Instagram issued a new token string, but GH_PAT isn't set, so it can't be saved. "
            "Paste a fresh token into the IG_ACCESS_TOKEN secret, or add GH_PAT (README step 6)."
        )
    subprocess.run(
        ["gh", "secret", "set", "IG_ACCESS_TOKEN", "--repo", os.environ["GITHUB_REPOSITORY"]],
        input=new_token.encode(),
        check=True,
        env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]},
    )
    log("Saved the new token to the IG_ACCESS_TOKEN secret.")


# --------------------------------------------------------------------------

def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "all"
    if command not in ("post", "reply", "all", "refresh-token", "check"):
        raise SystemExit("Usage: instagram_bot.py [post|reply|all|refresh-token|check]")

    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not token and os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        # Not set up yet - stay quiet instead of failing every 15 minutes.
        log("IG_ACCESS_TOKEN isn't set yet, so there's nothing to do. See README step 4.")
        return
    ig = Instagram(token)
    if is_dry_run():
        log("DRY RUN - nothing will be posted or sent.")

    if command == "check":
        log(f"Connected to Instagram as @{ig.user.get('username')} (id {ig.user_id}).")
        return
    if command == "refresh-token":
        run_refresh(ig)
        return

    failed = False
    if command in ("post", "all"):
        try:
            run_post(ig)
        except InstagramError as e:
            log(f"! Posting step failed: {e}")
            failed = True
    if command in ("reply", "all"):
        try:
            run_reply(ig)
        except InstagramError as e:
            log(f"! Reply step failed: {e}")
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
