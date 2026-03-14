# Reddit — community discovery and engagement

You can read Reddit without auth by appending `.json` to any URL. Use `curl` + `jq` for this.

## Search for subreddits

```bash
# find subreddits about a topic
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/subreddits/search.json?q=TOPIC&limit=10" | jq '.data.children[].data | {name: .display_name_prefixed, subscribers: .subscribers, description: .public_description[:100]}'

# example: find AI-related subreddits
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/subreddits/search.json?q=artificial+intelligence+agents&limit=10" | jq '.data.children[].data | {name: .display_name_prefixed, subscribers: .subscribers}'
```

## Search posts

```bash
# search posts across all of reddit
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/search.json?q=QUERY&sort=relevance&t=week&limit=10" | jq '.data.children[].data | {title: .title, subreddit: .subreddit_name_prefixed, score: .score, url: ("https://reddit.com" + .permalink), num_comments: .num_comments}'

# search within a specific subreddit
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/r/SUBREDDIT/search.json?q=QUERY&restrict_sr=on&sort=relevance&t=month&limit=10" | jq '.data.children[].data | {title: .title, score: .score, url: ("https://reddit.com" + .permalink)}'
```

## Read trending/hot posts

```bash
# hot posts in a subreddit
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/r/SUBREDDIT/hot.json?limit=10" | jq '.data.children[].data | {title: .title, score: .score, num_comments: .num_comments, url: ("https://reddit.com" + .permalink)}'

# top posts this week
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/r/SUBREDDIT/top.json?t=week&limit=10" | jq '.data.children[].data | {title: .title, score: .score}'
```

## Read a post and its comments

```bash
# read comments on a post (POST_ID is the alphanumeric id from the URL)
curl -s -H "User-Agent: groundcontrol/1.0" "https://www.reddit.com/comments/POST_ID.json" | jq '.[1].data.children[:10][].data | {author: .author, body: .body[:200], score: .score}'
```

## Tips for marketing on Reddit
- Always include `User-Agent` header or Reddit will block you
- Find subreddits where your project is genuinely relevant, don't spam
- Study what posts do well in the subreddit before posting
- Phrase posts as sharing knowledge, not promoting a product
- Good subreddits for dev tools: r/programming, r/MachineLearning, r/artificial, r/SideProject, r/webdev, r/Python, r/opensource, r/ChatGPT, r/LocalLLaMA
- Reddit cannot be posted to via this API without auth. Draft the post content and show it to the user for manual posting, or provide the subreddit URL to open
