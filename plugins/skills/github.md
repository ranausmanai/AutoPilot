# GitHub CLI

You have full access to the `gh` CLI tool. Use it to monitor repos, manage issues, PRs, and check project health.

## Repo Stats
- `gh api repos/OWNER/REPO --jq '.stargazers_count,.forks_count,.open_issues_count'` — stars, forks, issues count
- `gh api repos/OWNER/REPO --jq '{stars: .stargazers_count, forks: .forks_count, issues: .open_issues_count, watchers: .subscribers_count}'` — full stats
- `gh repo view OWNER/REPO --json stargazerCount,forkCount,description` — repo overview

## Issues
- `gh issue list -R OWNER/REPO` — list open issues
- `gh issue view NUMBER -R OWNER/REPO` — view a specific issue
- `gh issue create -R OWNER/REPO --title "title" --body "body"` — create an issue
- `gh issue comment NUMBER -R OWNER/REPO --body "comment"` — comment on an issue
- `gh issue close NUMBER -R OWNER/REPO` — close an issue

## Pull Requests
- `gh pr list -R OWNER/REPO` — list open PRs
- `gh pr view NUMBER -R OWNER/REPO` — view a PR
- `gh pr merge NUMBER -R OWNER/REPO` — merge a PR

## Releases
- `gh release list -R OWNER/REPO` — list releases
- `gh release create TAG -R OWNER/REPO --title "title" --notes "notes"` — create a release

## Activity
- `gh api repos/OWNER/REPO/events --jq '.[].type' | head -20` — recent repo events
- `gh api repos/OWNER/REPO/stargazers --jq '.[].login' | head -20` — recent stargazers
- `gh api repos/OWNER/REPO/traffic/views --jq '{views: .count, uniques: .uniques}'` — traffic (owner only)
- `gh api repos/OWNER/REPO/traffic/clones --jq '{clones: .count, uniques: .uniques}'` — clone stats (owner only)

## User
- `gh api user --jq '.login'` — your username
- `gh api users/USERNAME --jq '{followers: .followers, repos: .public_repos}'` — user stats

## Known Repos
The user's repos include:
- ranausmanai/AutoPilot — autonomous build + growth engine
- ranausmanai/AutoPrompt — evolutionary prompt optimization

## Tips
- Always use `-R OWNER/REPO` to target a specific repo
- Use `--jq` to extract specific fields from JSON
- For traffic data, you must be the repo owner
