# mastodon-digest

A daily "top posts" RSS feed for Mastodon hashtags. It's fully algorithmic, with no LLM involved. It runs on GitHub Actions.

## Rules
- **Schedule:** 05:00 UTC daily. Each run reports on the **previous UTC calendar day** (00:00–24:00).
- **Sources:** posts from `#LocalLLM #LocalLLMs #Ollama #llamacpp #LocalAI` on mastodon.social, merged, with duplicates and bots removed.
- **Eligibility:** a post needs at least 1 boost or 1 comment. If fewer than 10 posts qualify, the day lists fewer.
- **Ranking:** boosts + likes. At equal totals, boosts win, then comments, then the newer post.
- **Item:** one per day, titled `Local LLMs. Mastodon daily top. <Wkd> YYYY-MM-DD`, linking to https://mastodon.social/tags/LocalLLM.
- **Per post:** a linked first line, the author, `🔁 boosts · ❤️ likes · 💬 comments` (plus `🖼 n` for media), and the post text in a collapsible "Show post" block.
- Counts are as seen by mastodon.social at fetch time.

## Feed
`https://raw.githubusercontent.com/top-on/mastodon-digest/main/local-llm.xml`

## Testing offline
```
python3 digest.py --fixture day.json --now 2026-11-23T05:00:00Z --out /tmp/out
```
