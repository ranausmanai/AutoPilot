# Twitter CLI

You have full access to the `twitter` CLI tool. Use it to post, engage, search, and monitor Twitter/X.

## Posting
- `twitter post "text"` — post a tweet
- `twitter post "text" --image photo.jpg` — post with image (up to 4 with -i)
- `twitter reply TWEET_ID "text"` — reply to a tweet
- `twitter quote TWEET_ID "text"` — quote tweet
- `twitter retweet TWEET_ID` — retweet
- `twitter delete TWEET_ID` — delete a tweet (asks confirmation, pipe `echo y |` before it)

## Engagement
- `twitter like TWEET_ID` — like a tweet
- `twitter unlike TWEET_ID` — unlike
- `twitter bookmark TWEET_ID` — bookmark
- `twitter unbookmark TWEET_ID` — unbookmark

## Social
- `twitter follow USERNAME` — follow a user
- `twitter unfollow USERNAME` — unfollow
- `twitter followers USERNAME` — list someone's followers
- `twitter following USERNAME` — list who someone follows

## Reading & Search
- `twitter feed --type following -n 20 --json` — your chronological timeline
- `twitter feed --type for-you -n 20 --json` — algorithmic timeline
- `twitter search "query" --json -n 20` — search tweets
- `twitter search "query" --from USERNAME` — search tweets from a specific user
- `twitter search "query" --min-likes 100` — only popular tweets
- `twitter search "query" --since 2026-01-01 --until 2026-03-15` — date range
- `twitter search "query" --has images` — only tweets with images
- `twitter search "query" --exclude retweets --exclude replies` — only originals
- `twitter tweet TWEET_ID --json` — view a specific tweet and its replies
- `twitter user USERNAME --json` — view a user's profile
- `twitter user-posts USERNAME -n 10 --json` — a user's recent tweets
- `twitter likes USERNAME --json` — tweets liked by a user
- `twitter bookmarks --json` — your bookmarked tweets

## Identity
- `twitter whoami --json` — your own profile
- `twitter status` — check if authenticated

## Tips
- Always add `--json` when you need to parse output or extract tweet IDs
- Tweet IDs are in `data.id` in JSON output
- For threads: post first tweet, then reply to its ID, then reply to that reply's ID, etc.
- Max tweet length is 280 characters
- When searching for engagement metrics on your own tweets, use `twitter user-posts YOUR_USERNAME --json`
