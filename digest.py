#!/usr/bin/env python3
"""Mastodon hashtag daily top -> RSS. Fully algorithmic, no LLM.

For each feed in feeds.json:
  1. fetch recent posts for its hashtags from a Mastodon instance's public API
  2. merge + de-duplicate, keep posts from the previous UTC calendar day, drop bots
     and posts with neither a boost nor a comment (so a day can have < 10 posts)
  3. rank: boosts + likes, then boosts, then replies, then newest
  4. write one RSS item per run (the daily top list) to docs/<slug>.xml

Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape

USER_AGENT = "mastodon-digest/0.2"
ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Mastodon
# --------------------------------------------------------------------------- #
def http_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_tag(instance: str, tag: str, since: dt.datetime, max_pages: int = 30) -> list[dict]:
    """Page backwards through /api/v1/timelines/tag/<tag> until older than `since`."""
    posts: list[dict] = []
    max_id = None
    for _ in range(max_pages):
        params = {"limit": 40, **({"max_id": max_id} if max_id else {})}
        url = f"{instance.rstrip('/')}/api/v1/timelines/tag/{urllib.parse.quote(tag)}?" \
              + urllib.parse.urlencode(params)
        page = http_json(url)
        if not page:
            break
        posts.extend(page)
        max_id = page[-1]["id"]
        if parse_ts(page[-1]["created_at"]) < since:
            break
    return posts


_BARE_URL = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]]")


def _linkify(escaped: str) -> str:
    """Wrap bare http(s) URLs in already-escaped text in <a> tags."""
    return _BARE_URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)


class _ContentParser(HTMLParser):
    """Turns Mastodon status HTML into plain text and into safe HTML that keeps
    links (URLs, hashtags, mentions) clickable. Everything else is escaped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.html: list[str] = []
        self._spans: list[str] = []     # class of each open <span>
        self._in_link = False
        self._drop = 0                  # inside <script>/<style>

    @property
    def _hidden(self) -> bool:          # Mastodon hides URL prefixes/suffixes this way
        return any("invisible" in c for c in self._spans)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._drop += 1
        elif tag == "span":
            self._spans.append(a.get("class") or "")
        elif tag == "br":
            self.text.append("\n")
            self.html.append("<br>")
        elif tag == "a" and not self._in_link:
            href = a.get("href") or ""
            if href.startswith(("http://", "https://")):
                self.html.append(f'<a href="{html.escape(href)}">')
                self._in_link = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._drop:
            self._drop -= 1
        elif tag == "span" and self._spans:
            if "ellipsis" in self._spans.pop():     # shortened URL: show that it continues
                self.text.append("…")
                self.html.append("…")
        elif tag == "p":
            self.text.append("\n\n")
            self.html.append("<br><br>")
        elif tag == "a" and self._in_link:
            self.html.append("</a>")
            self._in_link = False

    def handle_data(self, data):
        if self._hidden or self._drop:
            return
        self.text.append(data)
        esc = html.escape(data)
        self.html.append(esc if self._in_link else _linkify(esc).replace("\n", "<br>"))


def parse_content(content: str) -> tuple[str, str]:
    """Returns (plain text, safe HTML with clickable links)."""
    p = _ContentParser()
    p.feed(content or "")
    p.close()
    if p._in_link:
        p.html.append("</a>")
    text = re.sub(r"\n{3,}", "\n\n", "".join(p.text)).strip()
    body = re.sub(r"(<br>\s*){3,}", "<br><br>", "".join(p.html))
    body = re.sub(r"^(\s|<br>)+|(\s|<br>)+$", "", body)
    return text, body


def html_to_text(content: str) -> str:
    return parse_content(content)[0]


def normalize(status: dict) -> dict:
    acct = status["account"]
    text, body_html = parse_content(status.get("content", ""))
    return {
        "body_html": body_html,
        "uri": status.get("uri") or status.get("url"),
        "url": status.get("url") or status.get("uri"),
        "created_at": status["created_at"],
        "author": acct.get("display_name") or acct.get("acct"),
        "acct": acct.get("acct"),
        "bot": bool(acct.get("bot")),
        "text": text,
        "spoiler": status.get("spoiler_text") or "",
        "media": len(status.get("media_attachments") or []),
        "boosts": status.get("reblogs_count", 0),
        "likes": status.get("favourites_count", 0),
        "replies": status.get("replies_count", 0),
    }


def rank_key(p: dict) -> tuple:
    """Boosts + likes first; at equal totals boosts win, then replies, then newest."""
    return (p["boosts"] + p["likes"], p["boosts"], p["replies"], p["created_at"])


def previous_day(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """[00:00, 24:00) UTC of the calendar day before `now`."""
    until = now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return until - dt.timedelta(days=1), until


def select_posts(raw: list[dict], since: dt.datetime, until: dt.datetime,
                 top_n: int, skip_bots: bool) -> list[dict]:
    seen, out = set(), []
    for s in raw:
        if s.get("reblog"):
            continue
        p = normalize(s)
        if p["uri"] in seen or not (since <= parse_ts(p["created_at"]) < until):
            continue
        if (skip_bots and p["bot"]) or (not p["text"] and not p["media"]):
            continue
        if p["boosts"] < 1 and p["replies"] < 1:   # needs at least one boost or comment
            continue
        seen.add(p["uri"])
        out.append(p)
    out.sort(key=rank_key, reverse=True)
    return out[:top_n]


# --------------------------------------------------------------------------- #
# RSS
# --------------------------------------------------------------------------- #
def first_line(text: str, limit: int = 90) -> str:
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + " …"


def post_html(p: dict, rank: int) -> str:
    meta = (f"{html.escape(p['author'])} (@{html.escape(p['acct'])}) · "
            f"🔁 {p['boosts']} · ❤️ {p['likes']} · 💬 {p['replies']}"
            + (f" · 🖼 {p['media']}" if p["media"] else ""))
    head = html.escape(first_line(p["text"]) or "(media only)")
    body = p["body_html"]
    label = f"CW: {html.escape(p['spoiler'])}" if p["spoiler"] else "Show post"
    return "\n".join([
        f"<h3>{rank}. <a href=\"{html.escape(p['url'])}\">{head}</a></h3>",
        f"<p><small>{meta}</small></p>",
        f"<details><summary>{label}</summary><p>{body}</p></details>",
    ])


def build_item(feed: dict, posts: list[dict], day: dt.datetime, now: dt.datetime) -> dict:
    body = [post_html(p, i + 1) for i, p in enumerate(posts)] or ["<p>No posts on this day.</p>"]
    return {
        "guid": f"daily-{feed['slug']}-{day:%Y-%m-%d}",     # one item per day; reruns replace it
        "title": f"{feed['title']}. {day:%a %Y-%m-%d}",
        "link": feed.get("link", ""),
        "pubDate": format_datetime(now),
        "html": "\n".join(body),
    }


def write_rss(path: Path, feed: dict, new_items: list[dict], max_items: int, now: dt.datetime) -> int:
    state_path = path.with_suffix(".items.json")
    old = json.loads(state_path.read_text()) if state_path.exists() else []
    merged, seen = [], set()
    for it in new_items + old:            # newest first, dedupe by guid
        if it["guid"] not in seen:
            seen.add(it["guid"])
            merged.append(it)
    merged = merged[:max_items]
    state_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1))

    tags = ", ".join("#" + t for t in feed["hashtags"])
    desc = f"Top {feed.get('top_n', 10)} Mastodon posts per day for {tags}, ranked by boosts + likes"
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', "<channel>",
         f"<title>{escape(feed['title'])}</title>",
         f"<link>{escape(feed.get('link', ''))}</link>",
         f"<description>{escape(desc)}</description>",
         f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"]
    if feed.get("self_url"):
        x.append(f'<atom:link href="{escape(feed["self_url"])}" rel="self" type="application/rss+xml"/>')
    for it in merged:
        x += ["<item>",
              f"<title>{escape(it['title'])}</title>",
              f"<link>{escape(it['link'])}</link>",
              f'<guid isPermaLink="false">{escape(it["guid"])}</guid>',
              f"<pubDate>{it['pubDate']}</pubDate>",
              f"<description>{escape(it['html'])}</description>",
              "</item>"]
    x += ["</channel>", "</rss>"]
    path.write_text("\n".join(x) + "\n", encoding="utf-8")
    return len(merged)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "feeds.json"))
    ap.add_argument("--out", default=str(ROOT))
    ap.add_argument("--fixture", help="JSON {tag: [statuses]} to use instead of the network")
    ap.add_argument("--now", help="override current time (ISO 8601)")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    now = parse_ts(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(Path(args.fixture).read_text()) if args.fixture else None

    failed = total = 0
    for feed in cfg["feeds"]:
        since, until = previous_day(now)
        instance = feed.get("instance", cfg.get("instance", "https://mastodon.social"))
        raw: list[dict] = []
        for tag in feed["hashtags"]:
            total += 1
            try:
                got = fixture.get(tag, []) if fixture is not None else fetch_tag(instance, tag, since)
                raw.extend(got)
                print(f"[{feed['slug']}] #{tag}: {len(got)} statuses", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                failed += 1
                print(f"[{feed['slug']}] #{tag}: fetch failed: {e}", file=sys.stderr)

        posts = select_posts(raw, since, until, feed.get("top_n", 10), feed.get("skip_bots", True))
        n = write_rss(out_dir / f"{feed['slug']}.xml", feed, [build_item(feed, posts, since, now)],
                      feed.get("max_items", 60), now)
        print(f"[{feed['slug']}] {len(posts)} posts, feed has {n} items", file=sys.stderr)

    return 1 if total and failed == total else 0


if __name__ == "__main__":
    sys.exit(main())
