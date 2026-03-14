# Hacker News — developer community engagement

HN has a fully public API. No auth needed for reading. Great for finding developer conversations and understanding what resonates.

## Search stories

```bash
# search HN stories via Algolia API
curl -s "https://hn.algolia.com/api/v1/search?query=QUERY&tags=story&hitsPerPage=10" | jq '.hits[] | {title: .title, points: .points, num_comments: .num_comments, url: .url, hn_url: ("https://news.ycombinator.com/item?id=" + (.objectID // "")), author: .author, created_at: .created_at}'

# search recent stories (last 24h)
curl -s "https://hn.algolia.com/api/v1/search_by_date?query=QUERY&tags=story&hitsPerPage=10" | jq '.hits[] | {title: .title, points: .points, url: .url}'

# search with filters: only popular posts
curl -s "https://hn.algolia.com/api/v1/search?query=QUERY&tags=story&numericFilters=points>50&hitsPerPage=10" | jq '.hits[] | {title: .title, points: .points, num_comments: .num_comments}'
```

## Read front page

```bash
# current top stories
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | jq '.[:10]' | while read id; do curl -s "https://hacker-news.firebaseio.com/v0/item/${id}.json" 2>/dev/null; done | jq '{title: .title, score: .score, url: .url, by: .by, descendants: .descendants}'

# simpler: use Algolia for front page
curl -s "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15" | jq '.hits[] | {title: .title, points: .points, num_comments: .num_comments, url: .url}'
```

## Read comments on a story

```bash
# get comments for a story (STORY_ID is the HN item ID number)
curl -s "https://hn.algolia.com/api/v1/search?tags=comment,story_STORY_ID&hitsPerPage=10" | jq '.hits[] | {author: .author, text: .comment_text[:200], points: .points}'
```

## Search for mentions of a project/tool

```bash
# find if anyone is talking about your project
curl -s "https://hn.algolia.com/api/v1/search?query=PROJECT_NAME&hitsPerPage=10" | jq '.hits[] | {title: .title, points: .points, url: .url, type: .type, hn_url: ("https://news.ycombinator.com/item?id=" + (.objectID // ""))}'
```

## Tips for marketing on Hacker News
- HN values technical depth, not hype. "Show HN" posts should be genuinely interesting
- Cannot post via API, only read. Draft Show HN posts for the user to submit manually
- Best time to post: weekday mornings US time (9-11am ET)
- Good "Show HN" format: "Show HN: [Name] - [one line what it does]"
- Include technical details that HN audience appreciates
- Don't game votes, HN detects and penalizes this
- Study what's trending to understand what resonates with the audience
