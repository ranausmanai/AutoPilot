#!/usr/bin/env python3
"""autopilot — autonomous build + grow agent.

Usage:
  # Marketing mode (default)
  python3 autopilot.py goal.md                              # interactive
  python3 autopilot.py goal.md --yolo                       # full auto
  python3 autopilot.py goal.md --yolo --check-every 1h      # daemon
  python3 autopilot.py goal.md --dry-run                    # plan only
  python3 autopilot.py goal.md --status                     # show log

  # Build + Ship mode  (develop overnight, tweet each iteration)
  python3 autopilot.py spec.md --build --iterations 10         # build, iterate, promote
  python3 autopilot.py spec.md --build --iterations 10 -e codex  # use codex
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

PROGRAM = Path(__file__).with_name("program.md")
BUILD_PROGRAM = Path(__file__).with_name("build_program.md")
PERSONALITY = Path(__file__).with_name("personality.md")
LOG_DIR = Path.home() / ".autopilot" / "logs"


def load_md(path):
    """Load a markdown file, return empty string if missing."""
    return path.read_text().strip() if path.exists() else ""
STRATEGY_DIR = Path.home() / ".autopilot" / "strategy"
BUILD_DIR = Path.home() / ".autopilot" / "builds"

# ─── Terminal UI helpers ────────────────────────────────────────────────────

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def banner(text, color=CYAN):
    width = 64
    print(f"\n{color}{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}{RESET}\n")


def step(icon, msg):
    print(f"  {icon}  {msg}")


def substep(msg):
    print(f"       {DIM}{msg}{RESET}")


def divider(label=""):
    if label:
        print(f"\n  {DIM}{'─' * 8} {label} {'─' * (48 - len(label))}{RESET}\n")
    else:
        print(f"  {DIM}{'─' * 60}{RESET}")


# ─── Persistent Log ─────────────────────────────────────────────────────────


class ActionLog:
    """Persistent log of all actions taken. Survives restarts. Prevents spam."""

    COOLDOWNS = {
        "twitter_post": timedelta(hours=3),
        "twitter_reply": timedelta(minutes=30),
        "reddit_post": timedelta(hours=24),
        "reddit_reply": timedelta(hours=1),
        "hn_post": timedelta(hours=24),
        "devto_post": timedelta(hours=24),
        "linkedin_post": timedelta(hours=12),
        "discover": timedelta(hours=2),
        "engage": timedelta(hours=1),
        "build": timedelta(seconds=0),
        "iterate": timedelta(seconds=0),
        "github": timedelta(minutes=5),
        "open_url": timedelta(seconds=0),
    }

    def __init__(self, goal_slug):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"{goal_slug}.jsonl"
        self.entries = self._load()

    def _load(self):
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def add(self, action, params, success, result):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "params": params,
            "success": success,
            "result": str(result)[:500],
        }
        self.entries.append(entry)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def is_on_cooldown(self, action, params):
        cooldown = self.COOLDOWNS.get(action, timedelta(0))
        if cooldown.total_seconds() == 0:
            return False, ""
        now = datetime.now()
        for entry in reversed(self.entries):
            if entry["action"] != action or not entry["success"]:
                continue
            entry_time = datetime.fromisoformat(entry["timestamp"])
            if now - entry_time < cooldown:
                remaining = cooldown - (now - entry_time)
                mins = int(remaining.total_seconds() / 60)
                if action == "reddit_post":
                    if entry["params"].get("subreddit") != params.get("subreddit"):
                        continue
                    return True, f"posted to r/{params.get('subreddit')} {mins}m ago"
                return True, f"last {action} was {mins}m ago"
        return False, ""

    def is_duplicate(self, action, params):
        for entry in self.entries:
            if not entry["success"] or entry["action"] != action:
                continue
            if action == "twitter_post":
                if entry["params"].get("text", "").strip() == params.get("text", "").strip():
                    return True, "exact same tweet already posted"
            elif action == "reddit_post":
                if (entry["params"].get("subreddit") == params.get("subreddit") and
                        entry["params"].get("title", "").strip() == params.get("title", "").strip()):
                    return True, f"same title already posted to r/{params.get('subreddit')}"
            elif action == "hn_post":
                if entry["params"].get("title", "").strip() == params.get("title", "").strip():
                    return True, "same HN title already posted"
        return False, ""

    def get_history_for_prompt(self):
        if not self.entries:
            return ""
        lines = ["\n\nACTION HISTORY (most recent last):"]
        for e in self.entries[-40:]:
            ts = e["timestamp"][:16].replace("T", " ")
            status = "OK" if e["success"] else "FAIL"
            action = e["action"]
            result = e["result"][:120]
            detail = ""
            if action == "twitter_post":
                detail = f' — "{e["params"].get("text", "")[:60]}..."'
            elif action == "reddit_post":
                detail = f' — r/{e["params"].get("subreddit", "?")} "{e["params"].get("title", "")[:40]}"'
            elif action in ("build", "iterate"):
                detail = f' — {e["result"][:60]}'
            elif action == "discover":
                detail = f' — found: {e["result"][:60]}'
            elif action == "github":
                detail = f' — {e["params"].get("command", "")[:60]}'
            lines.append(f"  [{ts}] [{status}] {action}{detail}")
            if status == "OK" and result:
                lines.append(f"           -> {result}")
        return "\n".join(lines)

    def summary(self):
        if not self.entries:
            print("  No actions taken yet.")
            return
        successes = [e for e in self.entries if e["success"]]
        failures = [e for e in self.entries if not e["success"]]
        print(f"  Total: {len(self.entries)} actions ({len(successes)} OK, {len(failures)} failed)")
        print()
        for e in self.entries:
            ts = e["timestamp"][:16].replace("T", " ")
            status = "OK" if e["success"] else "FAIL"
            print(f"  [{ts}] [{status}] {e['action']}: {e['result'][:80]}")


# ─── Strategy Memory ────────────────────────────────────────────────────────


class StrategyMemory:
    """Persistent memory of what works, discovered communities, and insights."""

    def __init__(self, goal_slug):
        STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
        self.path = STRATEGY_DIR / f"{goal_slug}.json"
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "discovered_communities": [],
            "insights": [],
            "what_worked": [],
            "what_failed": [],
            "posted_communities": [],
            "build_history": [],
        }

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2))

    def add_discovery(self, platform, community, reason):
        for d in self.data["discovered_communities"]:
            if d["platform"] == platform and d["community"] == community:
                return
        self.data["discovered_communities"].append({
            "platform": platform, "community": community,
            "reason": reason, "discovered": datetime.now().isoformat()[:10]
        })
        self.save()

    def add_insight(self, insight):
        self.data["insights"].append({
            "text": insight, "time": datetime.now().isoformat()[:16]
        })
        self.data["insights"] = self.data["insights"][-20:]
        self.save()

    def mark_posted(self, platform, community):
        self.data["posted_communities"].append({
            "platform": platform, "community": community,
            "time": datetime.now().isoformat()[:16]
        })
        self.save()

    def record_result(self, action, params, success, result):
        bucket = "what_worked" if success else "what_failed"
        self.data[bucket].append({
            "action": action, "summary": str(result)[:200],
            "time": datetime.now().isoformat()[:16],
        })
        self.data[bucket] = self.data[bucket][-15:]
        self.save()

    def add_build(self, iteration, features, output_dir):
        self.data["build_history"].append({
            "iteration": iteration,
            "features": features,
            "output_dir": str(output_dir),
            "time": datetime.now().isoformat()[:16],
        })
        self.save()

    def for_prompt(self):
        lines = []
        if self.data["discovered_communities"]:
            lines.append("\nDISCOVERED COMMUNITIES:")
            posted = {f"{p['platform']}:{p['community']}" for p in self.data["posted_communities"]}
            for d in self.data["discovered_communities"]:
                key = f"{d['platform']}:{d['community']}"
                status = " [ALREADY POSTED]" if key in posted else " [NOT YET POSTED]"
                lines.append(f"  - {d['platform']}/{d['community']}: {d['reason']}{status}")
        if self.data["build_history"]:
            lines.append("\nBUILD HISTORY:")
            for b in self.data["build_history"][-10:]:
                lines.append(f"  - iteration {b['iteration']}: {', '.join(b['features'][:3])}")
        if self.data["what_worked"]:
            lines.append("\nWHAT WORKED:")
            for w in self.data["what_worked"][-10:]:
                lines.append(f"  - {w['action']}: {w['summary'][:80]}")
        if self.data["what_failed"]:
            lines.append("\nWHAT FAILED:")
            for w in self.data["what_failed"][-10:]:
                lines.append(f"  - {w['action']}: {w['summary'][:80]}")
        if self.data["insights"]:
            lines.append("\nINSIGHTS:")
            for i in self.data["insights"][-10:]:
                lines.append(f"  - {i['text']}")
        return "\n".join(lines) if lines else ""


# ─── Measurement ─────────────────────────────────────────────────────────────


def measure_github_stars(repo):
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".stargazers_count"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None
    return None


def measure_progress(goal):
    metrics = {}
    repo_match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)', goal)
    if repo_match:
        repo = repo_match.group(1)
        stars = measure_github_stars(repo)
        if stars is not None:
            metrics["github_stars"] = stars
            metrics["github_repo"] = repo
    return metrics


# ─── Actions ─────────────────────────────────────────────────────────────────


def twitter_post(text):
    result = subprocess.run(
        ["twitter", "post", text],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return False, f"Twitter error: {result.stderr[:200]}"
    for line in result.stdout.splitlines():
        if "url:" in line.lower():
            return True, line.split("url:")[-1].strip()
    return True, "posted"


def twitter_reply(tweet_url, text):
    match = re.search(r'/status/(\d+)', tweet_url)
    if not match:
        return False, "Invalid tweet URL"
    result = subprocess.run(
        ["twitter", "reply", match.group(1), text],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return False, f"Twitter reply error: {result.stderr[:200]}"
    return True, "replied"


def reddit_post(subreddit, title, body):
    try:
        import browser_cookie3
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return False, "Missing deps: pip install browser-cookie3 curl-cffi"

    jar = browser_cookie3.chrome()
    cookie_parts = []
    for c in jar:
        if "reddit" in c.domain:
            cookie_parts.append(f"{c.name}={c.value}")
    cookie_string = "; ".join(cookie_parts)
    if not cookie_string:
        return False, "Not logged into Reddit in Chrome"

    session = cffi_requests.Session(impersonate="chrome131")
    headers = {
        "Cookie": cookie_string,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    resp = session.get("https://www.reddit.com/api/me.json", headers=headers)
    modhash = resp.json().get("data", {}).get("modhash", "")
    if not modhash:
        return False, "Could not get Reddit auth token"

    resp = session.post("https://www.reddit.com/api/submit", headers={
        **headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.reddit.com",
    }, data={
        "api_type": "json", "kind": "self", "sr": subreddit,
        "title": title, "text": body, "uh": modhash,
        "nsfw": "false", "spoiler": "false", "resubmit": "true", "sendreplies": "true",
    })

    result = resp.json()
    errors = result.get("json", {}).get("errors", [])
    if errors:
        return False, f"Reddit error: {errors}"
    return True, result.get("json", {}).get("data", {}).get("url", "")


def reddit_reply(post_url, text):
    try:
        import browser_cookie3
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return False, "Missing deps: pip install browser-cookie3 curl-cffi"

    jar = browser_cookie3.chrome()
    cookie_parts = [f"{c.name}={c.value}" for c in jar if "reddit" in c.domain]
    cookie_string = "; ".join(cookie_parts)
    if not cookie_string:
        return False, "Not logged into Reddit in Chrome"

    session = cffi_requests.Session(impersonate="chrome131")
    headers = {
        "Cookie": cookie_string,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    resp = session.get("https://www.reddit.com/api/me.json", headers=headers)
    modhash = resp.json().get("data", {}).get("modhash", "")
    if not modhash:
        return False, "Could not get Reddit auth token"

    json_url = post_url.rstrip("/") + ".json"
    resp = session.get(json_url, headers=headers)
    try:
        thing_id = resp.json()[0]["data"]["children"][0]["data"]["name"]
    except (KeyError, IndexError, json.JSONDecodeError):
        return False, "Could not get post ID from URL"

    resp = session.post("https://www.reddit.com/api/comment", headers={
        **headers, "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.reddit.com",
    }, data={"api_type": "json", "text": text, "thing_id": thing_id, "uh": modhash})

    errors = resp.json().get("json", {}).get("errors", [])
    if errors:
        return False, f"Reddit reply error: {errors}"
    return True, "replied"


def hn_post(title, url=None, text=None):
    cmd = ["hn", "submit", "--title", title]
    if url:
        cmd += ["--url", url]
    if text:
        cmd += ["--text", text]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return True, result.stdout.strip()[:200]
    submit_url = f"https://news.ycombinator.com/submitlink?u={url or ''}&t={title}"
    subprocess.run(["open", submit_url], capture_output=True)
    return True, f"opened HN submit page (manual post needed)"


def devto_post(title, body, tags=None):
    api_key = os.environ.get("DEVTO_API_KEY")
    if not api_key:
        subprocess.run(["open", "https://dev.to/new"], capture_output=True)
        return True, "opened dev.to/new (manual post — set DEVTO_API_KEY for auto)"
    import urllib.request
    data = json.dumps({"article": {
        "title": title, "body_markdown": body, "published": True, "tags": tags or [],
    }}).encode()
    req = urllib.request.Request(
        "https://dev.to/api/articles", data=data,
        headers={"api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return True, json.loads(resp.read()).get("url", "posted")
    except Exception as e:
        return False, str(e)[:200]


def linkedin_post(text):
    import urllib.parse
    url = f"https://www.linkedin.com/feed/?shareActive=true&text={urllib.parse.quote(text)}"
    subprocess.run(["open", url], capture_output=True)
    return True, "opened LinkedIn compose (needs manual submit)"


def discover_communities(query, engine):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    prompt = f"""Find 5-10 online communities where I can share this:

{query}

Return ONLY a JSON array (no markdown fences):
[{{"platform": "reddit", "community": "r/example", "reason": "why", "how_to_post": "how", "audience_size": "large"}}]

Focus on places where this would be WELCOMED."""

    cmd = (["claude", "-p", prompt, "--no-session-persistence"] if engine == "claude"
           else ["codex", "exec", prompt])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    if result.returncode != 0:
        return False, f"Discovery failed: {result.stderr[:200]}"
    try:
        return True, extract_json(result.stdout)
    except ValueError:
        return False, "Could not parse discovery results"


def engage_check(platform, post_url, engine):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    prompt = f"""Check this post: {post_url} on {platform}.
Return ONLY JSON (no fences):
{{"metrics": {{"upvotes": 0, "comments": 0}}, "replies_needed": [{{"comment_text": "...", "suggested_reply": "..."}}], "insight": "one sentence"}}
If you can't access it: {{"metrics": {{}}, "replies_needed": [], "insight": "unable to check"}}"""

    cmd = (["claude", "-p", prompt, "--no-session-persistence"] if engine == "claude"
           else ["codex", "exec", prompt])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    if result.returncode != 0:
        return False, f"Engage check failed: {result.stderr[:200]}"
    try:
        return True, extract_json(result.stdout)
    except ValueError:
        return False, "Could not parse engagement results"


def github_action(command):
    if not command.strip().startswith("gh "):
        return False, "Only gh commands are allowed"
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return False, result.stderr[:200]
    return True, result.stdout.strip()[:200]


def open_url(url):
    subprocess.run(["open", url], capture_output=True)
    return True, f"opened {url}"


ACTIONS = {
    "twitter_post": {
        "fn": lambda p: twitter_post(p["text"]),
        "desc": "Post a tweet (max 280 chars)",
        "params": ["text"], "cooldown": "3 hours",
    },
    "twitter_reply": {
        "fn": lambda p: twitter_reply(p["tweet_url"], p["text"]),
        "desc": "Reply to a tweet",
        "params": ["tweet_url", "text"], "cooldown": "30 minutes",
    },
    "reddit_post": {
        "fn": lambda p: reddit_post(p["subreddit"], p["title"], p["body"]),
        "desc": "Post to a subreddit",
        "params": ["subreddit", "title", "body"], "cooldown": "24h per subreddit",
    },
    "reddit_reply": {
        "fn": lambda p: reddit_reply(p["post_url"], p["text"]),
        "desc": "Reply to a Reddit post/comment",
        "params": ["post_url", "text"], "cooldown": "1 hour",
    },
    "hn_post": {
        "fn": lambda p: hn_post(p["title"], p.get("url"), p.get("text")),
        "desc": "Submit to Hacker News",
        "params": ["title", "url", "text"], "cooldown": "24 hours",
    },
    "devto_post": {
        "fn": lambda p: devto_post(p["title"], p["body"], p.get("tags")),
        "desc": "Publish on Dev.to",
        "params": ["title", "body", "tags"], "cooldown": "24 hours",
    },
    "linkedin_post": {
        "fn": lambda p: linkedin_post(p["text"]),
        "desc": "Post on LinkedIn",
        "params": ["text"], "cooldown": "12 hours",
    },
    "discover": {
        "fn": None, "desc": "Find new communities to post in",
        "params": ["query"], "cooldown": "2 hours",
    },
    "engage": {
        "fn": None, "desc": "Check previous posts for comments and reply",
        "params": ["platform", "post_url"], "cooldown": "1 hour",
    },
    "github": {
        "fn": lambda p: github_action(p["command"]),
        "desc": "Run a gh CLI command",
        "params": ["command"], "cooldown": "5 minutes",
    },
    "open_url": {
        "fn": lambda p: open_url(p["url"]),
        "desc": "Open a URL in browser",
        "params": ["url"], "cooldown": "none",
    },
}


# ─── LLM ─────────────────────────────────────────────────────────────────────


def llm(prompt, engine, reasoning=None, timeout=300):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if engine == "claude":
        result = subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    else:
        # only override reasoning if explicitly set, otherwise use codex config default
        cmd = ["codex", "exec"]
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        cmd.append(prompt)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail[:500] or f"{engine} failed")
    return result.stdout.strip()


def llm_agent(prompt, workdir, engine, timeout=None, reasoning=None):
    """Run LLM as an agent that can write files (for building code).
    Streams output live so you can see progress. No timeout by default."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    # ensure git repo (codex requires it, claude benefits from it)
    git_dir = workdir / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(workdir),
                       capture_output=True, timeout=10)

    if engine == "claude":
        cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions",
               "--no-session-persistence"]
    else:
        cmd = ["codex", "exec", "--full-auto"]
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        cmd.append(prompt)

    # stream output live + capture it
    print(f"\n       {CYAN}┌{'─' * 54}┐{RESET}")
    print(f"       {CYAN}│{RESET}  {BOLD}Agent working{RESET}  {DIM}dir: {workdir}{RESET}")
    print(f"       {CYAN}└{'─' * 54}┘{RESET}\n")

    output_lines = []
    agent_start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True, cwd=str(workdir), env=env,
        )

        import threading
        seen_files = {}  # filename -> (size, mtime)
        stop_watch = threading.Event()
        last_output_time = [time.time()]
        total_changes = [0]
        file_events = []  # queue of file events to print from main thread

        def watch_files():
            """Watch for new/modified files + show animated progress."""
            tick = 0
            while not stop_watch.is_set():
                tick += 1
                try:
                    current = {}
                    total_size = 0
                    for f in workdir.rglob("*"):
                        if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts and "node_modules" not in f.parts:
                            rel = str(f.relative_to(workdir))
                            try:
                                st = f.stat()
                                current[rel] = (st.st_size, st.st_mtime)
                                total_size += st.st_size
                            except OSError:
                                continue

                    # detect new and modified files
                    for name, (size, mtime) in current.items():
                        if name not in seen_files:
                            print(f"\033[2K       {GREEN}  + {name}{RESET} {DIM}({size:,} bytes){RESET}")
                            last_output_time[0] = time.time()
                            total_changes[0] += 1
                        elif seen_files[name][1] != mtime:
                            old_size = seen_files[name][0]
                            diff = size - old_size
                            diff_str = f"+{diff:,}" if diff >= 0 else f"{diff:,}"
                            print(f"\033[2K       {YELLOW}  ~ {name}{RESET} {DIM}({size:,} bytes, {diff_str}){RESET}")
                            last_output_time[0] = time.time()
                            total_changes[0] += 1

                    seen_files.update(current)

                    # always show animated progress bar
                    total_elapsed = time.time() - agent_start
                    t_mins = int(total_elapsed // 60)
                    t_secs = int(total_elapsed % 60)
                    size_str = f"{total_size / 1024:.1f}KB" if total_size > 1024 else f"{total_size}B"

                    # animated pulse bar
                    bar_width = 20
                    pos = tick % (bar_width * 2)
                    if pos >= bar_width:
                        pos = bar_width * 2 - pos
                    bar = ""
                    for i in range(bar_width):
                        dist = abs(i - pos)
                        if dist == 0:
                            bar += f"{CYAN}━{RESET}"
                        elif dist == 1:
                            bar += f"{CYAN}─{RESET}"
                        elif dist == 2:
                            bar += f"\033[2m─\033[0m"
                        else:
                            bar += f"\033[2m·\033[0m"

                    status = f"{len(current)} files, {size_str}"
                    time_str = f"{t_mins}m {t_secs:02d}s"

                    sys.stdout.write(f"\033[2K       {bar}  {BOLD}{time_str}{RESET} {DIM}{status}{RESET}\r")
                    sys.stdout.flush()

                except Exception:
                    pass
                stop_watch.wait(1)

        watcher = threading.Thread(target=watch_files, daemon=True)
        watcher.start()

        import select
        stall_limit = 600  # kill if no output for 10 minutes
        while True:
            # use select to wait for output with a timeout
            ready, _, _ = select.select([proc.stdout], [], [], 30)
            if ready:
                line = proc.stdout.readline()
                if not line:  # EOF — process done
                    break
                line = line.rstrip("\n")
                output_lines.append(line)
                last_output_time[0] = time.time()
                if line.strip():
                    print(f"\033[2K       {DIM}{line[:120]}{RESET}")
            else:
                # no output for 30s, check if stalled
                stall_time = time.time() - last_output_time[0]
                if stall_time > stall_limit:
                    print(f"\033[2K       {YELLOW}  ⚠ Agent stalled for {int(stall_time)}s, killing...{RESET}")
                    proc.kill()
                    break
                # also check if process has exited
                if proc.poll() is not None:
                    break

        proc.wait(timeout=30)
        stop_watch.set()
        watcher.join(timeout=5)

        elapsed = time.time() - agent_start
        e_mins = int(elapsed // 60)
        e_secs = int(elapsed % 60)
        file_count = len([f for f in workdir.rglob("*") if f.is_file() and ".git" not in f.parts])
        print(f"\033[2K\n       {CYAN}┌{'─' * 54}┐{RESET}")
        print(f"       {CYAN}│{RESET}  {BOLD}Agent finished{RESET}  {e_mins}m {e_secs}s, {file_count} files, {total_changes[0]} changes")
        print(f"       {CYAN}└{'─' * 54}┘{RESET}\n")

        output_text = "\n".join(output_lines)

        if proc.returncode != 0:
            if file_count > 0:
                return True, f"agent exited with error but wrote {file_count} files"
            return False, output_text.strip()[:500]
        return True, output_text.strip()[:500]

    except subprocess.TimeoutExpired:
        proc.kill()
        stop_watch.set()
        files = list(f for f in workdir.rglob("*") if f.is_file() and ".git" not in f.parts)
        if files:
            return True, f"agent timed out but wrote {len(files)} files"
        return False, "agent timed out with no output"
    except Exception as e:
        stop_watch.set() if 'stop_watch' in dir() else None
        return False, str(e)[:500]


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for left, right in (("[", "]"), ("{", "}")):
        start = text.find(left)
        end = text.rfind(right) + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in: {text[:300]}")


# ─── Build + Iterate Engine ─────────────────────────────────────────────────


def scan_project(workdir):
    """Scan a built project directory and return a summary of what exists."""
    files = []
    for f in sorted(workdir.rglob("*")):
        if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts and "node_modules" not in f.parts:
            rel = f.relative_to(workdir)
            size = f.stat().st_size
            files.append(f"{rel} ({size:,} bytes)")
    return files


def read_key_files(workdir, max_chars=30000):
    """Read the content of key files for the LLM to understand what was built."""
    key_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".json", ".yaml", ".yml", ".toml"}
    skip_files = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
    content_parts = []
    total = 0

    for f in sorted(workdir.rglob("*")):
        if not f.is_file() or f.suffix not in key_extensions:
            continue
        if f.name in skip_files or ".git" in f.parts or "node_modules" in f.parts:
            continue
        try:
            text = f.read_text(errors="ignore")
            if total + len(text) > max_chars:
                remaining = max_chars - total
                if remaining > 500:
                    content_parts.append(f"\n--- {f.relative_to(workdir)} (truncated) ---\n{text[:remaining]}")
                break
            content_parts.append(f"\n--- {f.relative_to(workdir)} ---\n{text}")
            total += len(text)
        except Exception:
            continue

    return "\n".join(content_parts)


def plan_features(spec, current_code, iteration, build_history, engine, reasoning):
    """LLM analyzes current build and decides what features to add next."""
    # load build program from external md file
    build_instructions = ""
    if BUILD_PROGRAM.exists():
        build_instructions = BUILD_PROGRAM.read_text().strip()

    history_str = ""
    if build_history:
        history_str = "\n\nPREVIOUS ITERATIONS:\n"
        for b in build_history:
            history_str += f"  iteration {b['iteration']}: added {', '.join(b['features'])}\n"

    prompt = f"""{build_instructions}

ORIGINAL SPEC:
{spec}

CURRENT CODEBASE:
{current_code}

ITERATION: {iteration}
{history_str}

Return ONLY a JSON object (no markdown fences):
{{
  "analysis": "one paragraph about what currently exists and how good it is",
  "features": [
    {{"name": "feature name", "description": "what to build and how", "priority": "high/medium", "wow_factor": "why this impresses users"}}
  ],
  "build_prompt": "detailed instructions for the developer to implement ALL the features above. be specific about file changes, new files needed, and how features should work. this is the actual prompt that will be sent to the coding agent."
}}"""

    result = llm(prompt, engine, reasoning, timeout=120)
    return extract_json(result)


def build_iteration(workdir, build_prompt, engine, iteration, reasoning=None):
    """Run the coding agent to build/iterate on the project."""
    step("🔨", f"Building iteration {iteration}...")
    start = time.time()

    success, output = llm_agent(build_prompt, workdir, engine, reasoning=reasoning)

    elapsed = time.time() - start
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    if success:
        step("✅", f"Build complete ({mins}m {secs}s)")
        # show what files exist now
        files = scan_project(workdir)
        substep(f"{len(files)} files in project")
        for f in files[:15]:
            substep(f"  {f}")
        if len(files) > 15:
            substep(f"  ... and {len(files) - 15} more")
    else:
        step("❌", f"Build failed ({mins}m {secs}s)")
        substep(output[:200])

    return success, output


def choose_repo_name(spec, engine):
    """LLM picks a catchy, available GitHub repo name."""
    prompt = f"""Pick a short, catchy, memorable name for this open source project.
The name should be:
- Lowercase, no spaces (hyphens ok)
- 3-15 characters
- Easy to say and remember
- Conveys what the project does

PROJECT SPEC:
{spec[:2000]}

Return ONLY a JSON object (no fences):
{{"name": "the-name", "tagline": "one line description under 100 chars"}}"""

    result = llm(prompt, engine, timeout=60)
    data = extract_json(result)
    return data.get("name", "my-project"), data.get("tagline", "")


def create_github_repo(name, tagline, workdir):
    """Create a public GitHub repo and ensure remote is configured."""
    repo_url = f"https://github.com/{name}"

    # check if repo already exists
    check = subprocess.run(
        ["gh", "repo", "view", name],
        capture_output=True, text=True, timeout=15
    )
    if check.returncode != 0:
        # create the repo (without --source, we'll add remote manually)
        subprocess.run(
            ["gh", "repo", "create", name, "--public", "--description", tagline],
            capture_output=True, text=True, timeout=30
        )

    # ensure remote is set up in workdir
    remote_check = subprocess.run(
        ["git", "remote", "-v"],
        cwd=str(workdir), capture_output=True, text=True, timeout=10
    )
    if "origin" not in remote_check.stdout:
        subprocess.run(
            ["git", "remote", "add", "origin", f"{repo_url}.git"],
            cwd=str(workdir), capture_output=True, timeout=10
        )
    else:
        # update remote URL in case it changed
        subprocess.run(
            ["git", "remote", "set-url", "origin", f"{repo_url}.git"],
            cwd=str(workdir), capture_output=True, timeout=10
        )

    return repo_url


def push_to_repo(workdir):
    """Push current state to the remote. Tries main, then master."""
    # check which branch we're on
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(workdir), capture_output=True, text=True, timeout=10
    )
    branch_name = branch.stdout.strip() or "main"

    # ensure remote exists
    remote_check = subprocess.run(
        ["git", "remote", "-v"],
        cwd=str(workdir), capture_output=True, text=True, timeout=10
    )
    if "origin" not in remote_check.stdout:
        return False, "no remote 'origin' configured"

    result = subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=str(workdir), capture_output=True, text=True, timeout=120
    )
    if result.returncode == 0:
        return True, f"pushed {branch_name}"

    # if branch doesn't exist upstream, try force
    result2 = subprocess.run(
        ["git", "push", "--set-upstream", "origin", branch_name],
        cwd=str(workdir), capture_output=True, text=True, timeout=120
    )
    if result2.returncode == 0:
        return True, f"pushed {branch_name}"

    return False, result.stderr[:200]


def verify_build(workdir, engine, reasoning=None):
    """Run the coding agent to test that everything works."""
    prompt = """You are a QA tester. Check this project:

1. Read all the code files
2. Look for obvious bugs, syntax errors, missing imports
3. If there's a package.json, check that dependencies make sense
4. If there's a Python project, try to import the main modules
5. If there's an HTML file, check it's valid
6. Run any existing tests if present
7. Try to start the app if possible (but don't leave it running)

Fix any issues you find. If something is broken, fix it.

Return a brief summary of what you tested and fixed."""

    success, output = llm_agent(prompt, workdir, engine, reasoning=reasoning)
    return success, output


def compose_update_tweet(project_name, iteration, features, repo_url, tagline, engine):
    """Use LLM to generate a natural, unique tweet for this iteration."""
    feature_list = ", ".join(f.get("name", "") for f in features[:3])
    personality = load_md(PERSONALITY)

    prompt = f"""Write a tweet about a project I'm building in public.

Project name: {project_name}
What it does: {tagline}
{"This is the LAUNCH tweet — first time sharing this project." if iteration == 1 else f"This is update #{iteration} — I just added new features."}
New features added: {feature_list}
Repo: {repo_url}

{personality}

ADDITIONAL RULES:
- The repo URL MUST be the last line, on its own
- Total tweet must be under 270 characters including the URL

Return ONLY the tweet text. Nothing else."""

    try:
        tweet = llm(prompt, engine, timeout=60).strip().strip('"').strip("'")
        # ensure repo URL is there
        if repo_url and repo_url not in tweet:
            tweet = tweet.rstrip() + f"\n{repo_url}"
        return tweet
    except Exception:
        # fallback
        if iteration == 1:
            return f"built {project_name}. {tagline.lower().rstrip('.')}. {feature_list}.\n{repo_url}"
        return f"update #{iteration} on {project_name}: added {feature_list}.\n{repo_url}"


def run_build_mode(spec_path, engine, reasoning, iterations):
    """The build + iterate + promote loop."""
    spec = spec_path.read_text().strip()
    goal_slug = slugify(spec_path.stem)

    # setup dirs
    workdir = BUILD_DIR / goal_slug
    workdir.mkdir(parents=True, exist_ok=True)

    log = ActionLog(goal_slug)
    strategy = StrategyMemory(goal_slug)

    banner(f"autopilot build mode", CYAN)
    step("📋", f"Spec: {spec_path}")
    step("📁", f"Build dir: {workdir}")
    step("🔄", f"Iterations: {iterations}")
    step("🤖", f"Engine: {engine}")
    print()

    # ── Phase 0: Pick name + create GitHub repo ──
    divider("SETUP")
    step("🏷️ ", "Choosing project name...")
    try:
        repo_name, tagline = choose_repo_name(spec, engine)
    except Exception:
        repo_name = goal_slug
        tagline = ""

    # get gh username
    gh_user = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True, text=True, timeout=15
    )
    gh_username = gh_user.stdout.strip() if gh_user.returncode == 0 else ""
    full_repo_name = f"{gh_username}/{repo_name}" if gh_username else repo_name

    project_name = repo_name  # use the chosen name, not the filename
    step("✅", f"Name: {BOLD}{project_name}{RESET} — {tagline}")
    print()

    step("🐙", "Creating GitHub repo...")

    # init git in workdir first
    git_dir = workdir / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(workdir), capture_output=True, timeout=10)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=str(workdir), capture_output=True, timeout=10)

    repo_url = create_github_repo(full_repo_name, tagline, workdir)
    step("✅", f"Repo: {BOLD}{repo_url}{RESET}")
    print()

    # ── Main loop ──
    for iteration in range(1, iterations + 1):
        iter_start = time.time()
        divider(f"ITERATION {iteration}/{iterations}")

        # ── Phase 1: Analyze + Plan features ──
        step("🧠", "Analyzing project and planning features...")

        has_files = any(f for f in workdir.iterdir() if f.name != ".git")
        current_code = read_key_files(workdir) if has_files else "(empty project — first build)"
        build_history = strategy.data.get("build_history", [])

        try:
            plan = plan_features(spec, current_code, iteration, build_history, engine, reasoning)
        except Exception as e:
            step("❌", f"Planning failed: {e}")
            log.add("iterate", {"iteration": iteration}, False, str(e))
            continue

        analysis = plan.get("analysis", "")
        features = plan.get("features", [])
        build_prompt = plan.get("build_prompt", "")

        step("📊", f"Analysis:")
        for line in textwrap.wrap(analysis, 80):
            substep(line)
        print()
        step("🎯", f"Features for this iteration:")
        for f in features:
            print(f"       {GREEN}+{RESET} {BOLD}{f.get('name', '')}{RESET}")
            desc = f.get('description', '')
            for line in textwrap.wrap(desc, 75):
                print(f"         {line}")
            if f.get('wow_factor'):
                print(f"         {DIM}wow: {f['wow_factor'][:100]}{RESET}")
        print()

        # ── Phase 2: Build (let it work as long as it needs) ──
        if iteration == 1:
            full_prompt = f"""Build this project from scratch based on this spec:

{spec}

Additional instructions:
{build_prompt}

Create all necessary files. Make it fully functional and complete.
Take your time, quality matters more than speed.

IMPORTANT: Create a beautiful README.md for GitHub:
- Project name as heading with a short tagline
- Badges (license, python version, etc.)
- A "Quick Start" section with installation and usage commands
- A "Features" section with what it does
- If the architecture is non-trivial, add an SVG diagram
- Use emojis tastefully in section headers
- Make it look professional and polished, like a popular open source project
- Include a screenshot section placeholder if applicable"""
        else:
            full_prompt = f"""You are iterating on an existing project. Here's what to do:

{build_prompt}

IMPORTANT:
- Do NOT rewrite files from scratch. Modify existing files.
- Keep everything that already works.
- Add the new features on top of what exists.
- Make sure nothing breaks.
- Update README.md if new features warrant it.
- Take your time — get it right."""

        success, output = build_iteration(workdir, full_prompt, engine, iteration, reasoning)

        feature_names = [f.get("name", "unknown") for f in features]
        log.add("build", {"iteration": iteration, "features": feature_names},
                success, output[:200])
        strategy.add_build(iteration, feature_names, workdir)

        if not success:
            step("⚠️ ", "Build failed — retrying with fix prompt...")
            fix_prompt = f"""The previous build attempt had issues:
{output[:1000]}

Fix ALL the errors and make sure the project works. Read the existing files first,
understand what's there, then fix the problems. Take your time."""
            success, output = build_iteration(workdir, fix_prompt, engine, iteration, reasoning)
            if not success:
                step("❌", "Retry also failed. Moving on.")
                continue

        # ── Phase 3: Test every 2 iterations ──
        if iteration % 2 == 0 or iteration == 1:
            print()
            step("🧪", f"Testing iteration {iteration}...")
            test_ok, test_output = verify_build(workdir, engine, reasoning)
            if test_ok:
                step("✅", "Tests passed")
                substep(test_output[:150])
                log.add("test", {"iteration": iteration}, True, test_output[:200])
            else:
                step("⚠️ ", "Tests found issues — fixing...")
                substep(test_output[:150])
                log.add("test", {"iteration": iteration}, False, test_output[:200])
                # the verify_build already fixes issues it finds,
                # but run one more fix pass if it failed
                fix_prompt = f"""The tests found problems:
{test_output[:1000]}

Fix everything. Make sure the project runs correctly."""
                fix_ok, fix_out = llm_agent(fix_prompt, workdir, engine, reasoning=reasoning)
                if fix_ok:
                    step("✅", "Fixes applied")

        # ── Phase 4: Git commit + push ──
        subprocess.run(["git", "add", "-A"], cwd=str(workdir),
                       capture_output=True, timeout=15)
        commit_msg = f"iteration {iteration}: {', '.join(feature_names)}"
        subprocess.run(["git", "commit", "-m", commit_msg],
                       cwd=str(workdir), capture_output=True, timeout=15)
        substep(f"committed: {commit_msg}")

        # push to GitHub
        push_ok, push_msg = push_to_repo(workdir)
        if push_ok:
            step("✅", f"Pushed to {repo_url}")
        else:
            step("⚠️ ", f"Push failed: {push_msg}")

        # ── Phase 5: Tweet about it ──
        print()
        tweet = compose_update_tweet(project_name, iteration, features, repo_url, tagline, engine)
        step("🐦", f"Tweet:")
        for line in tweet.splitlines():
            print(f"       {CYAN}{line}{RESET}")

        on_cd, _ = log.is_on_cooldown("twitter_post", {"text": tweet})
        is_dup, _ = log.is_duplicate("twitter_post", {"text": tweet})

        if on_cd:
            substep("twitter on cooldown — skipping")
        elif is_dup:
            substep("duplicate tweet — skipping")
        elif len(tweet) > 280:
            substep(f"tweet too long ({len(tweet)} chars) — skipping")
        else:
            try:
                ok, result = twitter_post(tweet)
                if ok:
                    step("✅", f"Tweeted: {result}")
                    log.add("twitter_post", {"text": tweet}, True, result)
                else:
                    step("⚠️ ", f"Tweet failed: {result}")
                    log.add("twitter_post", {"text": tweet}, False, result)
            except Exception as e:
                step("⚠️ ", f"Tweet error: {e}")
                log.add("twitter_post", {"text": tweet}, False, str(e))

        # ── Phase 6: Cool down ──
        iter_elapsed = time.time() - iter_start
        iter_mins = int(iter_elapsed // 60)
        iter_secs = int(iter_elapsed % 60)
        print()
        step("⏱️ ", f"Iteration {iteration} took {iter_mins}m {iter_secs}s")

        if iteration < iterations:
            print()
            step("⏳", f"Cooling down 120s before next iteration...")
            for remaining in range(120, 0, -30):
                time.sleep(30)
                if remaining > 30:
                    substep(f"{remaining - 30}s remaining...")

    # ── Final Summary ──
    banner("BUILD COMPLETE", GREEN)
    step("📁", f"Project: {workdir}")
    step("🐙", f"Repo: {repo_url}")
    step("🔄", f"Iterations: {iterations}")

    files = scan_project(workdir)
    step("📄", f"Total files: {len(files)}")

    builds = strategy.data.get("build_history", [])
    all_features = []
    for b in builds:
        all_features.extend(b.get("features", []))
    step("🎯", f"Features built: {len(all_features)}")
    for feat in all_features:
        substep(f"+ {feat}")

    successes = sum(1 for e in log.entries if e["success"])
    failures = sum(1 for e in log.entries if not e["success"])
    step("📊", f"Actions: {successes} OK, {failures} failed")
    step("📝", f"Log: {log.path}")
    step("🧠", f"Strategy: {strategy.path}")
    print()


# ─── Growth Mode Planner ────────────────────────────────────────────────────


def plan_actions(goal, log, strategy, metrics, engine, reasoning):
    actions_desc = "\n".join(
        f"- {name}: {a['desc']} (params: {', '.join(a['params'])}) [cooldown: {a['cooldown']}]"
        for name, a in ACTIONS.items()
    )
    history_str = log.get_history_for_prompt()
    strategy_str = strategy.for_prompt()
    metrics_str = ""
    if metrics:
        metrics_str = "\n\nCURRENT METRICS:\n" + "".join(f"  {k}: {v}\n" for k, v in metrics.items())

    growth_program = load_md(PROGRAM)
    personality = load_md(PERSONALITY)

    prompt = f"""{growth_program}

{personality}

GOAL:
{goal}

AVAILABLE ACTIONS:
{actions_desc}
{metrics_str}
{strategy_str}
{history_str}

CRITICAL:
- Plan 1-4 actions. Quality over quantity.
- DO NOT repeat succeeded actions from history.
- Every post must have unique content and angle.
- No repeated/stuttered words in posts.
- No preamble. Return ONLY a JSON array (no markdown fences):

[{{"action": "name", "params": {{}}, "reason": "why"}}]

Return [] if done."""

    return extract_json(llm(prompt, engine, reasoning))


# ─── Utils ──────────────────────────────────────────────────────────────────


def parse_interval(s):
    total = 0
    for match in re.finditer(r'(\d+)\s*(h|m|s)', s.lower()):
        val, unit = int(match.group(1)), match.group(2)
        total += val * {"h": 3600, "m": 60, "s": 1}[unit]
    return total or 600


def slugify(text):
    return (re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "goal")[:48]


# ─── Execute Round (Growth Mode) ────────────────────────────────────────────

COMMON_WORDS = {"the","a","an","and","or","to","in","of","is","it","i","my","for","on","with",
                "that","this","was","but","not","you","your","are","be","have","had","has","do",
                "did","at","from","by","as","so","if","up","out","no","just","its","can","all",
                "get","got","one","been","into","than","then","them","they","what","when","how","about"}


def execute_round(goal, log, strategy, engine, reasoning, dry_run, yolo):
    metrics = measure_progress(goal)
    if metrics:
        step("📈", f"Metrics: {', '.join(f'{k}={v}' for k, v in metrics.items())}")

    step("🧠", "Planning...")

    try:
        actions = plan_actions(goal, log, strategy, metrics, engine, reasoning)
    except Exception as e:
        step("❌", f"Planning failed: {e}")
        return 0

    if not actions:
        step("🎯", "Nothing to do — goal may be achieved")
        return -1

    step("📋", f"{len(actions)} actions planned")
    print()

    executed = 0
    for i, action in enumerate(actions):
        name = action.get("action", "?")
        params = action.get("params", {})
        reason = action.get("reason", "")

        print(f"  [{i+1}/{len(actions)}] {BOLD}{name}{RESET}")
        substep(reason)

        # show details
        if name == "twitter_post":
            print(f'         {CYAN}"{params.get("text", "")}"{RESET}')
        elif name == "reddit_post":
            print(f'         {CYAN}r/{params.get("subreddit", "?")} — {params.get("title", "")}{RESET}')
            body = params.get("body", "")
            if body:
                print(f'         body:')
                for line in body.splitlines():
                    print(f'           {DIM}{line}{RESET}')
        elif name == "hn_post":
            print(f'         {CYAN}{params.get("title", "")}{RESET}')
        elif name == "devto_post":
            print(f'         {CYAN}{params.get("title", "")}{RESET}')
            body = params.get("body", "")
            if body:
                for line in body.splitlines()[:10]:
                    print(f'           {DIM}{line}{RESET}')
        elif name == "linkedin_post":
            print(f'         {CYAN}{params.get("text", "")[:150]}{RESET}')
        elif name == "discover":
            print(f'         query: {params.get("query", "")}')
        elif name == "engage":
            print(f'         {params.get("platform", "")} — {params.get("post_url", "")}')
        elif name == "github":
            print(f'         $ {params.get("command", "")}')

        # repetition check
        if name in ("twitter_post", "reddit_post", "linkedin_post"):
            text_to_check = params.get("text", "") or params.get("body", "")
            words = text_to_check.lower().split()
            bad = next((w for w in set(words) if len(w) > 2 and w not in COMMON_WORDS and words.count(w) > 3), None)
            if bad:
                print(f"         {RED}-> BLOCKED: repetitive ('{bad}' x{words.count(bad)}){RESET}\n")
                log.add(name, params, False, "repetitive text blocked")
                continue

        if name not in ACTIONS:
            print(f"         {RED}-> BLOCKED: unknown action{RESET}\n")
            log.add(name, params, False, "unknown action")
            continue

        is_dup, dup_reason = log.is_duplicate(name, params)
        if is_dup:
            print(f"         {YELLOW}-> BLOCKED: {dup_reason}{RESET}\n")
            continue

        on_cd, cd_reason = log.is_on_cooldown(name, params)
        if on_cd:
            print(f"         {YELLOW}-> BLOCKED: {cd_reason}{RESET}\n")
            continue

        if dry_run:
            print(f"         {DIM}-> SKIPPED (dry-run){RESET}\n")
            dry_run_path = log.path.with_suffix(".dry-run.json")
            try:
                existing = json.loads(dry_run_path.read_text()) if dry_run_path.exists() else []
            except (json.JSONDecodeError, OSError):
                existing = []
            existing.append({"action": name, "params": params, "reason": reason})
            dry_run_path.write_text(json.dumps(existing, indent=2))
            continue

        if not yolo:
            try:
                answer = input(f"         -> Execute? [y/n/edit] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer == "n":
                log.add(name, params, False, "skipped by user")
                continue
            elif answer == "edit":
                if name == "twitter_post":
                    new = input("         new tweet: ").strip()
                    if new:
                        params["text"] = new
                elif name == "reddit_post":
                    new = input("         new title (enter to keep): ").strip()
                    if new:
                        params["title"] = new
                    new = input("         new body (enter to keep): ").strip()
                    if new:
                        params["body"] = new

        # execute
        try:
            if name == "discover":
                success, result = discover_communities(params.get("query", goal), engine)
                if success and isinstance(result, list):
                    for comm in result:
                        strategy.add_discovery(comm.get("platform", ""), comm.get("community", ""), comm.get("reason", ""))
                    result_str = f"found {len(result)}: {', '.join(c.get('community','?') for c in result[:5])}"
                    print(f"         {GREEN}-> OK: {result_str}{RESET}\n")
                    log.add(name, params, True, result_str)
                    executed += 1
                else:
                    print(f"         {RED}-> FAILED: {result}{RESET}\n")
                    log.add(name, params, False, str(result))

            elif name == "engage":
                success, result = engage_check(params.get("platform", ""), params.get("post_url", ""), engine)
                if success and isinstance(result, dict):
                    m = result.get("metrics", {})
                    replies = result.get("replies_needed", [])
                    insight = result.get("insight", "")
                    result_str = f"metrics: {m}, {len(replies)} replies"
                    print(f"         {GREEN}-> OK: {result_str}{RESET}\n")
                    log.add(name, params, True, result_str)
                    if insight:
                        strategy.add_insight(insight)
                    executed += 1
                else:
                    print(f"         {RED}-> FAILED: {result}{RESET}\n")
                    log.add(name, params, False, str(result))

            else:
                success, result = ACTIONS[name]["fn"](params)
                color = GREEN if success else RED
                print(f"         {color}-> {'OK' if success else 'FAILED'}: {result}{RESET}\n")
                log.add(name, params, success, result)
                strategy.record_result(name, params, success, result)
                if success:
                    if name == "reddit_post":
                        strategy.mark_posted("reddit", params.get("subreddit", ""))
                    elif name == "hn_post":
                        strategy.mark_posted("hn", "hackernews")
                    elif name == "devto_post":
                        strategy.mark_posted("devto", "dev.to")
                    executed += 1

        except Exception as e:
            print(f"         {RED}-> ERROR: {e}{RESET}\n")
            log.add(name, params, False, str(e))

        time.sleep(2)

    return executed


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="autopilot — autonomous build + growth agent")
    p.add_argument("goal", help="goal file (.md) or spec file for --build mode")
    p.add_argument("-e", "--engine", default="claude", choices=["claude", "codex"])
    p.add_argument("--reasoning", default=None, choices=["low", "medium", "high", "xhigh"],
                   help="override codex reasoning effort (default: use codex config)")
    p.add_argument("--dry-run", action="store_true", help="plan but don't execute")
    p.add_argument("--yolo", action="store_true", help="auto-approve all actions")
    p.add_argument("--rounds", type=int, default=5, help="max rounds (default: 5)")
    p.add_argument("--check-every", type=str, default=None,
                   help="daemon mode: check every interval (e.g. 30m, 1h)")
    p.add_argument("--status", action="store_true", help="show action log and exit")
    # build mode
    p.add_argument("--build", action="store_true",
                   help="build mode: develop project from spec, iterate, and promote")
    p.add_argument("--iterations", type=int, default=5,
                   help="number of build iterations (default: 5)")
    args = p.parse_args()

    goal_path = Path(args.goal)
    if goal_path.exists():
        goal = goal_path.read_text().strip()
        goal_slug = slugify(goal_path.stem)
    else:
        goal = args.goal
        goal_slug = slugify(goal[:48])

    # ── Build Mode ──
    if args.build:
        if not goal_path.exists():
            print(f"  {RED}Error: spec file not found: {args.goal}{RESET}")
            sys.exit(1)
        run_build_mode(goal_path, args.engine, args.reasoning, args.iterations)
        return

    # ── Status Mode ──
    log = ActionLog(goal_slug)
    strategy = StrategyMemory(goal_slug)

    if args.status:
        banner(f"autopilot status — {goal_slug}", CYAN)
        log.summary()
        if strategy.data["discovered_communities"]:
            print(f"\n  Discovered communities:")
            for c in strategy.data["discovered_communities"]:
                print(f"    {GREEN}+{RESET} {c['platform']}/{c['community']}: {c['reason']}")
        if strategy.data["build_history"]:
            print(f"\n  Build history:")
            for b in strategy.data["build_history"]:
                print(f"    iteration {b['iteration']}: {', '.join(b['features'])}")
        if strategy.data["insights"]:
            print(f"\n  Insights:")
            for i in strategy.data["insights"]:
                print(f"    {DIM}- {i['text']}{RESET}")
        print()
        return

    # ── Growth Mode ──
    is_daemon = args.check_every is not None
    interval = parse_interval(args.check_every) if is_daemon else 0
    mode = "dry-run" if args.dry_run else "yolo" if args.yolo else "interactive"

    banner("autopilot — autonomous growth agent", CYAN)
    step("🎯", f"Goal: {goal[:80]}{'...' if len(goal) > 80 else ''}")
    step("🤖", f"Engine: {args.engine}")
    step("📋", f"Mode: {mode}")
    step("📝", f"Log: {log.path}")
    if is_daemon:
        step("🔄", f"Daemon: checking every {args.check_every}")
    else:
        step("🔄", f"Rounds: {args.rounds}")
    print()

    if is_daemon:
        round_num = 0
        while True:
            round_num += 1
            divider(f"Check #{round_num} at {datetime.now().strftime('%H:%M')}")
            result = execute_round(goal, log, strategy, args.engine, args.reasoning,
                                   args.dry_run, args.yolo)
            if result == -1:
                break
            step("⏳", f"Next check in {args.check_every}...")
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                print(f"\n  {YELLOW}Stopped by user.{RESET}")
                break
    else:
        for round_num in range(1, args.rounds + 1):
            divider(f"Round {round_num}/{args.rounds}")
            result = execute_round(goal, log, strategy, args.engine, args.reasoning,
                                   args.dry_run, args.yolo)
            if result == -1:
                break

    # final summary
    successes = sum(1 for e in log.entries if e["success"])
    failures = sum(1 for e in log.entries if not e["success"])
    metrics = measure_progress(goal)

    banner("AUTOPILOT DONE", GREEN)
    step("📊", f"Actions: {successes} succeeded, {failures} failed")
    if metrics:
        for k, v in metrics.items():
            step("📈", f"{k}: {v}")
    if strategy.data["discovered_communities"]:
        posted = {f"{p['platform']}:{p['community']}" for p in strategy.data["posted_communities"]}
        unposted = sum(1 for c in strategy.data["discovered_communities"]
                       if f"{c['platform']}:{c['community']}" not in posted)
        step("🌍", f"Communities: {len(strategy.data['discovered_communities'])} discovered, {unposted} untried")
    dry_run_path = log.path.with_suffix(".dry-run.json")
    if dry_run_path.exists():
        step("👁️ ", f"Preview: {dry_run_path}")
    step("📝", f"Log: {log.path}")
    step("🧠", f"Strategy: {strategy.path}")
    print()


if __name__ == "__main__":
    main()
