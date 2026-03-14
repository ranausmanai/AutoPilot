# 🚀 AutoPilot

**Autonomous build + growth engine. It develops your project, pushes to GitHub, tweets updates, and iterates overnight.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-orange)](autopilot.py)
[![Lines of Code](https://img.shields.io/badge/LOC-~1650-purple)](autopilot.py)

Give it a spec. Walk away. Come back to a built project on GitHub with update tweets for each iteration.

AutoPilot combines **building** (like autoship) with **marketing** (social media, community discovery, engagement) in a single autonomous loop.

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│   spec.md ──► plan features ──► build code ──► test      │
│                                                  │       │
│                                          commit + push   │
│                                                  │       │
│                                          tweet update    │
│                                                  │       │
│                                          cooldown ──► ↺  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## ✨ Features

### 🔨 Build Mode
- Reads a product spec, plans features, builds code using Claude or Codex
- Auto-creates GitHub repo with LLM-chosen name
- Tests every 2 iterations, auto-fixes failures
- Commits, pushes, and tweets progress after each iteration
- No timeouts — lets the AI agent work as long as it needs
- Streaming output with animated progress bar and file watcher

### 📣 Growth Mode
- Discovers communities where your project belongs
- Posts to Twitter, Reddit, Hacker News, Dev.to, LinkedIn
- Tracks what worked and what flopped
- Engages with comments and replies
- Persistent strategy memory — learns across runs
- Cooldown system prevents spam

### 🎨 Fully Configurable
All prompts are in markdown files you can edit:

| File | Controls |
|------|----------|
| `program.md` | Growth strategy, platform playbook, discovery rules |
| `build_program.md` | Feature planning, build process, testing, README style |
| `personality.md` | Voice, tone, banned words, platform-specific writing style |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI) or [Codex](https://github.com/openai/codex) (`codex` CLI)
- [GitHub CLI](https://cli.github.com/) (`gh`) — for repo creation and push
- [twitter-cli](https://github.com/sferik/t) — for tweeting (optional)

### Build Mode (the main thing)

```bash
git clone https://github.com/ranausmanai/AutoPilot.git
cd AutoPilot

# build a project from spec, push to github, tweet updates
python3 autopilot.py examples/test_spec.md --build -e codex

# use claude instead
python3 autopilot.py examples/test_spec.md --build -e claude

# set number of iterations
python3 autopilot.py examples/test_spec.md --build -e codex --iterations 5

# set reasoning effort (codex only)
python3 autopilot.py examples/test_spec.md --build -e codex --reasoning medium
```

### Growth Mode

```bash
# promote a project across social media
python3 autopilot.py examples/example_goal.md -e claude

# dry run — see what it would do without posting
python3 autopilot.py examples/example_goal.md -e claude --dry-run

# limit rounds
python3 autopilot.py examples/example_goal.md -e claude --rounds 3
```

---

## 🔄 Build Mode Flow

Here's what happens when you run `--build`:

1. **Read spec** — parses your markdown spec file
2. **Create GitHub repo** — LLM picks a name, `gh repo create` sets it up
3. **Plan features** — LLM reads the spec + existing code and picks 2-4 features
4. **Build** — Codex/Claude writes the code (no timeout, streaming output)
5. **Test** — every 2 iterations, LLM verifies the build and fixes issues
6. **Commit + Push** — auto-commits with descriptive message, pushes to GitHub
7. **Tweet** — composes an update tweet about what was built
8. **Cooldown** — waits 120s, then loops back to step 3

The terminal shows a live progress bar with file detection:

```
  ⠋ Building... 45s | 3 files | 12.4 KB
    + index.html (2.1 KB)
    + style.css (856 B)
    ~ app.js (9.4 KB → 12.4 KB)
```

---

## 📣 Growth Mode Flow

1. **Plan** — LLM reads your goal + past history and decides what to do
2. **Discover** — finds relevant communities (subreddits, forums, etc.)
3. **Post** — writes platform-appropriate content and posts it
4. **Engage** — checks previous posts for comments, replies thoughtfully
5. **Learn** — records what worked, updates strategy for next round

Strategy persists across runs at `~/.autopilot/strategy/`.

---

## ⚙️ Options

| Flag | Description | Default |
|------|-------------|---------|
| `--build` | Enable build mode (develop + iterate + tweet) | Off |
| `-e, --engine` | LLM backend: `claude` or `codex` | `claude` |
| `--iterations` | Max build iterations | `10` |
| `--rounds` | Max growth mode rounds | `5` |
| `--dry-run` | Plan actions without executing | Off |
| `--reasoning` | Codex reasoning effort: `low`, `medium`, `high`, `xhigh` | Codex default |

---

## 📂 Project Structure

```
AutoPilot/
├── autopilot.py          # the engine (~1650 lines)
├── program.md            # growth mode instructions (editable)
├── build_program.md      # build mode instructions (editable)
├── personality.md        # voice & tone rules (editable)
├── examples/
│   ├── example_goal.md   # sample growth mode goal
│   ├── test_spec.md      # sample build spec (GitPulse)
│   └── quick_test.md     # minimal spec for pipeline testing
├── LICENSE
└── README.md
```

---

## 🎭 Personality

AutoPilot generates all content (tweets, Reddit posts, commit messages) using rules from `personality.md`. Out of the box:

- Writes like a developer, not a marketer
- No AI slop (no "excited to announce", no "game-changer", no em dashes)
- No hashtags, no emojis in tweets
- Platform-appropriate tone (casual on Twitter, story-driven on Reddit, technical on HN)

Edit `personality.md` to change the voice to match yours.

---

## 💾 Where Things Live

| What | Where |
|------|-------|
| Built projects | `~/.autopilot/builds/<project-name>/` |
| Action logs | `~/.autopilot/logs/` |
| Strategy memory | `~/.autopilot/strategy/` |
| Dry run output | `.dry-run.json` in current directory |

---

## 🧠 Tips

- **Start with `--dry-run`** in growth mode to see what it would post before going live
- **Use `quick_test.md`** to verify the build pipeline works before running a real spec
- **Edit `personality.md`** first — the default voice might not match yours
- **Check `~/.autopilot/builds/`** to inspect what was built
- **Reasoning effort matters** — `medium` is fast, `xhigh` is thorough but slow. For quick tests, use `medium`

---

## 🤝 Contributing

Found a bug? Have ideas? PRs welcome.

---

## 📄 License

[MIT](LICENSE)
