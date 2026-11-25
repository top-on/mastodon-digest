# mastodon-digest

A daily "top posts" RSS feed for Mastodon hashtags. It's fully algorithmic, with no LLM involved. It runs on GitHub Actions.

## Feeds

| Feed | Hashtags | RSS URL |
|---|---|---|
| **Local LLMs** | #LocalLLM #LocalLLMs #Ollama #llamacpp #LocalAI | `https://raw.githubusercontent.com/top-on/mastodon-digest/main/local-llm.xml` |
| **Meshcore** | #meshcore | `https://raw.githubusercontent.com/top-on/mastodon-digest/main/meshcore.xml` |
| **40C3** | #40c3 | `https://raw.githubusercontent.com/top-on/mastodon-digest/main/40c3.xml` |

Add the RSS URL to any feed reader. Each feed gets one new item per day.

To add a feed, add an entry to `feeds.json`. It becomes `<slug>.xml`, and all feeds are built in the same run.

## Rules
- **Schedule:** 03:23 UTC daily, with a backup run at 04:47 UTC. Each run reports on the **previous UTC calendar day** (00:00–24:00), so the backup only refreshes that day's item or fills in if the first run was skipped.
- **Sources:** each feed's hashtags on mastodon.social, merged, with duplicates and bots removed.
- **Eligibility:** a post needs at least 1 boost or 1 comment. If fewer than 10 posts qualify, the day lists fewer.
- **Ranking:** boosts + likes. At equal totals, boosts win, then comments, then the newer post.
- **Item:** one per day, titled `<feed title>. <Wkd> YYYY-MM-DD`, linking to this repo.
- **Per post:** a linked first line, the author, `🔁 boosts · ❤️ likes · 💬 comments` (plus `🖼 n` for media), and the post text in a collapsible "Show post" block.
- Counts are as seen by mastodon.social at fetch time.

## Testing offline
```
python3 digest.py --fixture day.json --now 2026-11-23T05:00:00Z --out /tmp/out
```
