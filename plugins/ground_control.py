#!/usr/bin/env python3
"""ground control — your command center for autopilot, from discord.

Run parallel builds, tweet, post threads, search twitter, check github
stats, monitor engagement, and more. Talk naturally or use commands.

Skills are loaded from markdown files in skills/ — drop a new .md file
to teach ground control new CLI tools. No code changes needed.

Setup:
  1. Create a bot at https://discord.com/developers/applications
  2. Copy the bot token
  3. Invite bot to your server (Send Messages, Read Message History)
  4. Run: python3 ground_control.py --token YOUR_TOKEN --owner YOUR_USER_ID

Talk naturally:
  "build me a todo app with dark mode"
  "how's AutoPilot doing on github?"
  "search tweets about AI agents and like the best ones"
  "write a thread about my new project and post it"
  "who starred my repo today?"
  "check my twitter engagement this week"
  "how many followers do I have?"
  "stop the weather app build"
"""

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import discord

# ─── Config ──────────────────────────────────────────────────────────────────

AUTOPILOT = Path(__file__).resolve().parent.parent / "autopilot.py"
AUTOSHIP = Path(__file__).resolve().parent.parent.parent / "autoship" / "autoship.py"
BUILDS_DIR = Path.home() / ".autopilot" / "builds"
LOGS_DIR = Path.home() / ".autopilot" / "logs"
STRATEGY_DIR = Path.home() / ".autopilot" / "strategy"
SPECS_DIR = Path.home() / ".autopilot" / "specs"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
TOOLS_DIR = Path(__file__).resolve().parent / "tools"

for d in [BUILDS_DIR, LOGS_DIR, STRATEGY_DIR, SPECS_DIR, TOOLS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def load_skills():
    """Load all skill markdown files from the skills/ directory."""
    if not SKILLS_DIR.exists():
        return ""
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        skills.append(f.read_text().strip())
    return "\n\n---\n\n".join(skills)


# ─── Build Manager ───────────────────────────────────────────────────────────

class BuildManager:
    """Tracks multiple parallel builds."""

    def __init__(self):
        self.builds = {}  # name -> {process, thread, log_lines, start_time, spec, channel}

    def is_running(self, name):
        b = self.builds.get(name)
        return b and b["process"] and b["process"].poll() is None

    def active_builds(self):
        return {k: v for k, v in self.builds.items() if self.is_running(k)}

    def all_builds(self):
        return dict(self.builds)

    def get_status(self, name):
        b = self.builds.get(name)
        if not b:
            return None
        elapsed = time.time() - b["start_time"]
        running = self.is_running(name)
        return {
            "name": name,
            "running": running,
            "elapsed": elapsed,
            "log_lines": b["log_lines"],
            "spec": b["spec"],
            "returncode": b["process"].returncode if b["process"] and not running else None,
        }

    def get_summary(self):
        """Get a text summary of all builds for LLM context."""
        if not self.builds:
            return "No builds have been started."

        parts = []
        for name, b in self.builds.items():
            elapsed = time.time() - b["start_time"]
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            running = self.is_running(name)
            status = "RUNNING" if running else f"DONE (exit {b['process'].returncode})"
            recent = b["log_lines"][-10:] if b["log_lines"] else ["(no output)"]

            # check strategy for features/repo
            strategy = get_strategy(name)
            repo = strategy.get("repo_url", "") if strategy else ""
            features = []
            if strategy:
                for bh in strategy.get("build_history", []):
                    features.extend(bh.get("features", []))

            parts.append(f"BUILD: {name}\n"
                         f"  Status: {status}\n"
                         f"  Elapsed: {mins}m {secs}s\n"
                         f"  Repo: {repo or 'not created yet'}\n"
                         f"  Features: {', '.join(features[-8:]) or 'none yet'}\n"
                         f"  Recent output:\n    " + "\n    ".join(recent))

        return "\n\n".join(parts)

    async def start_build(self, name, spec_text, channel, loop,
                          engine="codex", iterations=5, reasoning=None):
        """Start a build in a background thread."""
        spec_path = SPECS_DIR / f"{name}.md"
        spec_path.write_text(spec_text)

        cmd = [sys.executable, str(AUTOPILOT), str(spec_path),
               "--build", "-e", engine, "--iterations", str(iterations)]
        if reasoning:
            cmd += ["--reasoning", reasoning]

        log_lines = []
        start_time = time.time()

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, env=env,
        )

        self.builds[name] = {
            "process": proc,
            "thread": None,
            "log_lines": log_lines,
            "start_time": start_time,
            "spec": spec_text,
            "channel": channel,
        }

        def run():
            for line in proc.stdout:
                line = line.rstrip()
                if line.strip():
                    log_lines.append(line)
                    # send milestones to discord
                    if any(kw in line for kw in [
                        "ITERATION", "Analyzing", "Building", "Testing",
                        "Pushed", "Tweet", "COMPLETE", "failed", "error",
                        "Repo:", "Name:"
                    ]):
                        asyncio.run_coroutine_threadsafe(
                            channel.send(f"`[{name}]` ```\n{line[:300]}\n```"),
                            loop
                        )
            proc.wait()

            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            rc = proc.returncode

            strategy = get_strategy(name)
            fields = [("Time", f"{mins}m {secs}s", True)]
            if strategy:
                repo = strategy.get("repo_url", "")
                features = []
                for bh in strategy.get("build_history", []):
                    features.extend(bh.get("features", []))
                if repo:
                    fields.append(("Repo", repo, False))
                if features:
                    feat_list = "\n".join(f"+ {f}" for f in features[-10:])
                    fields.append(("Features", f"```\n{feat_list}\n```", False))

            color = 0x2ea043 if rc == 0 else 0xda3633
            asyncio.run_coroutine_threadsafe(
                channel.send(embed=make_embed(
                    f"{'Build Complete' if rc == 0 else 'Build Failed'}: {name}",
                    "", color=color, fields=fields,
                )),
                loop
            )

        thread = threading.Thread(target=run, daemon=True)
        self.builds[name]["thread"] = thread
        thread.start()

    def stop_build(self, name):
        b = self.builds.get(name)
        if b and b["process"] and b["process"].poll() is None:
            os.kill(b["process"].pid, signal.SIGTERM)
            return True
        return False

    def stop_all(self):
        stopped = []
        for name in list(self.builds.keys()):
            if self.stop_build(name):
                stopped.append(name)
        return stopped


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_embed(title, description="", color=0x58a6ff, fields=None):
    embed = discord.Embed(title=title, description=description[:4096], color=color,
                          timestamp=datetime.now())
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value[:1024], inline=inline)
    embed.set_footer(text="ground control")
    return embed


def get_strategy(project_name):
    path = STRATEGY_DIR / f"{project_name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def tail_lines(path, n=20):
    try:
        lines = Path(path).read_text().strip().splitlines()
        return lines[-n:]
    except Exception:
        return []


def format_log_entry(line):
    try:
        entry = json.loads(line)
        action = entry.get("action", "?")
        ok = "+" if entry.get("ok") else "-"
        detail = entry.get("detail", "")[:80]
        ts = entry.get("timestamp", "")[:19]
        return f"`{ts}` {ok} **{action}** {detail}"
    except Exception:
        return line[:100]


# ─── LLM for conversational mode ────────────────────────────────────────────

def llm_respond(user_message, build_summary, engine="claude"):
    """Send user message + build context to LLM, get response + optional action."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    skills = load_skills()

    prompt = f"""You are Ground Control, the discord assistant for AutoPilot (an autonomous build + growth engine).
You run on the user's machine and have access to CLI tools. You can run any command.

CURRENT STATE:
{build_summary}

AVAILABLE PROJECTS:
{list_projects_text()}

TOOLS & SKILLS:
{skills}

USER MESSAGE: {user_message}

Respond conversationally in 1-3 sentences. Be concise and helpful. Sound like a teammate, not a bot.

If the user wants you to DO something, include ACTION blocks at the end of your response.
You can include MULTIPLE actions — they run in sequence.

Built-in actions:
ACTION: {{"type": "build", "spec": "the spec text", "name": "project-name"}}
ACTION: {{"type": "autoship", "spec": "the spec text", "slug": "app-name", "engine": "claude"}}
ACTION: {{"type": "stop", "name": "project-name"}}
ACTION: {{"type": "stop_all"}}
ACTION: {{"type": "restart"}}
ACTION: {{"type": "thread", "tweets": ["tweet 1", "tweet 2", "tweet 3"]}}

Self-improvement (create reusable tools and skills):
ACTION: {{"type": "create_tool", "name": "tool_name.py", "code": "#!/usr/bin/env python3\\n..."}}
ACTION: {{"type": "create_skill", "name": "skill_name.md", "content": "# Skill Name\\n..."}}

Generic shell action (use this for twitter CLI, gh CLI, or any command):
ACTION: {{"type": "run", "command": "twitter post \\"hello world\\"", "label": "Posting tweet"}}
ACTION: {{"type": "run", "command": "gh api repos/owner/repo --jq '.stargazers_count'", "label": "Checking stars"}}
ACTION: {{"type": "run", "command": "python3 plugins/tools/my_tool.py args", "label": "Running custom tool"}}

You can chain multiple run actions:
ACTION: {{"type": "run", "command": "twitter search \\"AI agents\\" --json -n 5", "label": "Searching tweets"}}
ACTION: {{"type": "run", "command": "twitter like TWEET_ID", "label": "Liking tweet"}}

Rules:
- Always add --json flag when you need to parse output from twitter or gh commands
- For threads: use the built-in thread action, not multiple run actions
- Keep tweets under 280 characters
- Only include ACTION if the user clearly wants something done
- For status/info questions, answer from your knowledge and context
- Do NOT use em dashes. Do not be overly enthusiastic. Be direct.
- When the user asks "how is X doing" about a project, check both GitHub stats AND recent tweets about it
- If you can't do something with existing skills, CREATE a new tool and skill for it. Write a script to plugins/tools/, document it in plugins/skills/, then use it. You are self-improving.
- Tools you create persist. Next time someone asks for the same thing, the skill will already be loaded."""

    try:
        if engine == "claude":
            result = subprocess.run(
                ["claude", "-p", prompt, "--no-session-persistence"],
                capture_output=True, text=True, timeout=60, env=env,
            )
        else:
            result = subprocess.run(
                ["codex", "exec", prompt],
                capture_output=True, text=True, timeout=60, env=env,
            )
        if result.returncode != 0:
            return "Something went wrong talking to the LLM.", None
        return parse_llm_response(result.stdout.strip())
    except subprocess.TimeoutExpired:
        return "LLM took too long to respond.", None
    except Exception as e:
        return f"Error: {e}", None


def parse_llm_response(text):
    """Split LLM response into message and list of actions."""
    actions = []
    message_lines = []

    for line in text.split("\n"):
        match = re.match(r'ACTION:\s*(\{.*\})', line.strip())
        if match:
            try:
                actions.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass
        else:
            message_lines.append(line)

    message = "\n".join(message_lines).strip()
    return message, actions


def list_projects_text():
    """Get a text list of projects for LLM context."""
    if not BUILDS_DIR.exists():
        return "None"
    projects = [d.name for d in BUILDS_DIR.iterdir() if d.is_dir()]
    if not projects:
        return "None"
    lines = []
    for p in sorted(projects):
        strategy = get_strategy(p)
        repo = strategy.get("repo_url", "") if strategy else ""
        lines.append(f"- {p}" + (f" ({repo})" if repo else ""))
    return "\n".join(lines)


# ─── Social actions ──────────────────────────────────────────────────────────

def do_tweet(text):
    """Post a tweet using twitter-cli."""
    try:
        result = subprocess.run(
            ["twitter", "post", "--json", text],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        try:
            data = json.loads(output)
            if data.get("ok"):
                tweet_id = data.get("data", {}).get("id", "")
                url = data.get("data", {}).get("url", "")
                return True, url or f"Posted (id: {tweet_id})"
            else:
                err = data.get("error", {}).get("message", "Unknown error")
                return False, err
        except json.JSONDecodeError:
            if result.returncode == 0:
                return True, output[:200]
            return False, output[:200]
    except FileNotFoundError:
        return False, "twitter-cli not installed"
    except Exception as e:
        return False, str(e)


def do_thread(tweets):
    """Post a Twitter thread. tweets is a list of strings."""
    if not tweets:
        return False, "No tweets to post"

    results = []
    last_id = None

    for i, tweet_text in enumerate(tweets):
        try:
            if i == 0:
                # first tweet
                cmd = ["twitter", "post", "--json", tweet_text]
            else:
                # reply to previous tweet
                cmd = ["twitter", "reply", "--json", last_id, tweet_text]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout.strip()

            try:
                data = json.loads(output)
                if data.get("ok"):
                    last_id = data.get("data", {}).get("id", "")
                    url = data.get("data", {}).get("url", "")
                    results.append(f"Tweet {i+1}: {url}")
                else:
                    err = data.get("error", {}).get("message", "Unknown error")
                    results.append(f"Tweet {i+1}: FAILED - {err}")
                    return False, "\n".join(results)
            except json.JSONDecodeError:
                if result.returncode == 0:
                    results.append(f"Tweet {i+1}: posted")
                else:
                    results.append(f"Tweet {i+1}: FAILED - {output[:100]}")
                    return False, "\n".join(results)

        except Exception as e:
            results.append(f"Tweet {i+1}: FAILED - {e}")
            return False, "\n".join(results)

    return True, "\n".join(results)


# ─── Bot ─────────────────────────────────────────────────────────────────────

def create_bot(token, channel_id, owner_id, engine="claude"):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    builds = BuildManager()

    @client.event
    async def on_ready():
        print(f"  ground control online as {client.user}")
        if channel_id:
            print(f"  watching channel: {channel_id}")
            ch = client.get_channel(channel_id)
            if ch:
                await ch.send(embed=make_embed(
                    "Ground Control Online",
                    "Talk to me naturally or use `!help` for commands.\n"
                    "I can run parallel builds, tweet, post to reddit, and more.",
                    color=0x58a6ff,
                ))
        print(f"  DMs: enabled")
        if owner_id:
            print(f"  owner: {owner_id}")

    @client.event
    async def on_message(message):
        if message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_allowed_channel = channel_id and message.channel.id == channel_id
        is_owner = owner_id and message.author.id == owner_id

        # allow: DMs from owner (or anyone if no owner set), or the designated channel
        if is_dm:
            if owner_id and not is_owner:
                return  # DM from someone else, ignore
        elif not is_allowed_channel:
            return

        content = message.content.strip()
        if not content:
            return

        loop = asyncio.get_event_loop()

        # ── Commands (start with !) ──
        if content.startswith("!"):
            parts = content.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd == "!help":
                await message.channel.send(embed=make_embed(
                    "Ground Control",
                    "Talk naturally or use commands. I can run twitter, github, and autopilot commands.",
                    color=0x58a6ff,
                    fields=[
                        ("!build <spec>", "Start a build (parallel OK)", False),
                        ("!status", "Show all active builds", False),
                        ("!stop <name>", "Stop a build (!stop all for all)", False),
                        ("!tweet <text>", "Post a tweet", False),
                        ("!thread <topic>", "Compose and post a Twitter thread", False),
                        ("!logs [project]", "Show recent logs", False),
                        ("!projects", "List all projects", False),
                        ("!restart", "Restart the bot (picks up new skills/code)", False),
                        ("Natural language", "\"how's AutoPilot doing on github?\"\n"
                         "\"search tweets about AI agents\"\n"
                         "\"like that tweet\"\n"
                         "\"check my follower count\"\n"
                         "\"who starred my repo today?\"", False),
                    ]
                ))

            elif cmd == "!build":
                spec_text = args

                if message.attachments:
                    for att in message.attachments:
                        if att.filename.endswith(".md"):
                            spec_text = (await att.read()).decode("utf-8")
                            break

                if not spec_text:
                    await message.channel.send(
                        "Send a spec with the command or attach a `.md` file.\n"
                        "Example: `!build A todo app with dark mode`")
                    return

                # generate a short name
                name = re.sub(r'[^a-z0-9]+', '-', spec_text[:40].lower()).strip('-')
                if not name:
                    name = f"build-{int(time.time())}"

                if builds.is_running(name):
                    await message.channel.send(f"`{name}` is already running.")
                    return

                await message.channel.send(embed=make_embed(
                    f"Build Started: {name}",
                    f"```\n{spec_text[:500]}\n```",
                    color=0x2ea043,
                    fields=[
                        ("Engine", engine, True),
                        ("Parallel builds", str(len(builds.active_builds()) + 1), True),
                    ]
                ))
                await builds.start_build(name, spec_text, message.channel, loop, engine=engine)

            elif cmd == "!status":
                active = builds.active_builds()
                if not active:
                    await message.channel.send(embed=make_embed(
                        "No Active Builds",
                        "Nothing running. Use `!build` to start one.",
                        color=0x8b949e,
                    ))
                    return

                for name in active:
                    status = builds.get_status(name)
                    mins = int(status["elapsed"] // 60)
                    secs = int(status["elapsed"] % 60)
                    recent = status["log_lines"][-10:] or ["(no output yet)"]
                    log_text = "\n".join(recent)
                    if len(log_text) > 900:
                        log_text = log_text[-900:]

                    await message.channel.send(embed=make_embed(
                        f"Building: {name}",
                        f"```\n{log_text}\n```",
                        color=0xff9f1c,
                        fields=[("Elapsed", f"{mins}m {secs}s", True)],
                    ))

            elif cmd == "!stop":
                target = args.strip()
                if target == "all":
                    stopped = builds.stop_all()
                    if stopped:
                        await message.channel.send(embed=make_embed(
                            "All Builds Stopped",
                            "Killed: " + ", ".join(stopped),
                            color=0xda3633,
                        ))
                    else:
                        await message.channel.send("Nothing running.")
                elif target:
                    if builds.stop_build(target):
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{target}**", color=0xda3633,
                        ))
                    else:
                        await message.channel.send(f"No running build named `{target}`.")
                else:
                    active = list(builds.active_builds().keys())
                    if len(active) == 1:
                        builds.stop_build(active[0])
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{active[0]}**", color=0xda3633,
                        ))
                    elif active:
                        await message.channel.send(
                            f"Multiple builds running: {', '.join(active)}\n"
                            f"Use `!stop <name>` or `!stop all`.")
                    else:
                        await message.channel.send("Nothing running.")

            elif cmd == "!tweet":
                if not args:
                    await message.channel.send("Usage: `!tweet your tweet text here`")
                    return
                ok, result = do_tweet(args)
                color = 0x2ea043 if ok else 0xda3633
                await message.channel.send(embed=make_embed(
                    "Tweeted" if ok else "Tweet Failed",
                    result, color=color,
                ))

            elif cmd == "!thread":
                if not args:
                    await message.channel.send(
                        "Tell me what to thread about and I'll compose it.\n"
                        "Example: `!thread write a thread about my AutoPilot project`")
                    return
                # use LLM to compose the thread
                async with message.channel.typing():
                    compose_result = await loop.run_in_executor(
                        None, llm_respond,
                        f"Write a Twitter thread about: {args}\n\n"
                        f"Return it as ACTION: {{\"type\": \"thread\", \"tweets\": [\"tweet1\", \"tweet2\", ...]}}.\n"
                        f"Each tweet must be under 280 characters. Make it engaging. 4-7 tweets is ideal. "
                        f"First tweet should hook. Last tweet should have a call to action.",
                        builds.get_summary(), engine
                    )
                response_text, thread_actions = compose_result
                if response_text:
                    await message.channel.send(response_text[:2000])
                thread_action = next((a for a in thread_actions if a.get("type") == "thread"), None)
                if thread_action:
                    tweets = thread_action.get("tweets", [])
                    if tweets:
                        # show preview
                        preview = "\n\n".join(f"**{i+1}.** {t}" for i, t in enumerate(tweets))
                        await message.channel.send(embed=make_embed(
                            f"Posting thread ({len(tweets)} tweets)...",
                            preview[:4000], color=0x1da1f2,
                        ))
                        ok, result = do_thread(tweets)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Thread Posted" if ok else "Thread Failed",
                            result[:2000], color=color,
                        ))

            elif cmd == "!logs":
                project = args.strip()
                if not project:
                    logs = list(LOGS_DIR.glob("*.jsonl"))
                    if logs:
                        names = "\n".join(f"- `{l.stem}`" for l in sorted(logs)[-10:])
                        await message.channel.send(embed=make_embed(
                            "Available Logs", names, color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send("No logs found.")
                    return

                log_path = LOGS_DIR / f"{project}.jsonl"
                if not log_path.exists():
                    await message.channel.send(f"No log for `{project}`")
                    return
                lines = tail_lines(log_path, 10)
                formatted = "\n".join(format_log_entry(l) for l in lines)
                await message.channel.send(embed=make_embed(
                    f"Logs: {project}", formatted[:2000], color=0x58a6ff,
                ))

            elif cmd == "!projects":
                if not BUILDS_DIR.exists():
                    await message.channel.send("No projects built yet.")
                    return
                projects = [d for d in BUILDS_DIR.iterdir() if d.is_dir()]
                if not projects:
                    await message.channel.send("No projects built yet.")
                    return
                lines = []
                for p in sorted(projects):
                    files = list(f for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
                    total_size = sum(f.stat().st_size for f in files)
                    size_str = f"{total_size/1024:.0f}KB" if total_size > 1024 else f"{total_size}B"
                    strategy = get_strategy(p.name)
                    repo = strategy.get("repo_url", "") if strategy else ""
                    repo_str = f" [{repo}]" if repo else ""
                    lines.append(f"**{p.name}** — {len(files)} files, {size_str}{repo_str}")
                await message.channel.send(embed=make_embed(
                    "Projects", "\n".join(lines[:15]), color=0x58a6ff,
                ))

            elif cmd == "!restart":
                await message.channel.send(embed=make_embed(
                    "Restarting...",
                    "Picking up new skills and code. Back in a sec.",
                    color=0xff9f1c,
                ))
                await client.close()
                os.execv(sys.executable, [sys.executable] + sys.argv)

            return  # handled command, don't fall through to conversational

        # ── Conversational mode (no ! prefix) ──
        # multi-step loop: run actions, feed results back to LLM if needed
        user_message = content
        max_steps = 4

        for step in range(max_steps):
            async with message.channel.typing():
                build_summary = builds.get_summary()
                response, actions = await loop.run_in_executor(
                    None, llm_respond, user_message, build_summary, engine
                )

            if response:
                await message.channel.send(response[:2000])

            if not actions:
                break

            # collect run outputs for follow-up
            run_outputs = []

            for action in actions:
                action_type = action.get("type")

                if action_type == "build":
                    spec = action.get("spec", "")
                    name = action.get("name", re.sub(r'[^a-z0-9]+', '-', spec[:40].lower()).strip('-'))
                    if spec:
                        await message.channel.send(embed=make_embed(
                            f"Starting build: {name}",
                            f"```\n{spec[:300]}\n```",
                            color=0x2ea043,
                        ))
                        await builds.start_build(name, spec, message.channel, loop, engine=engine)

                elif action_type == "autoship":
                    spec = action.get("spec", "")
                    slug = action.get("slug", "")
                    eng = action.get("engine", engine)
                    if spec:
                        if not slug:
                            slug = re.sub(r'[^a-z0-9]+', '-', spec[:40].lower()).strip('-')
                        spec_path = SPECS_DIR / f"{slug}.md"
                        spec_path.write_text(spec)
                        await message.channel.send(embed=make_embed(
                            f"AutoShip: {slug}",
                            f"Building and deploying to `{slug}.autoship.fun`\n```\n{spec[:400]}\n```",
                            color=0x2ea043,
                        ))
                        cmd = [sys.executable, str(AUTOSHIP), str(spec_path),
                               "-e", eng, "--deploy", "autoship", "--slug", slug]

                        def run_autoship(cmd=cmd, slug=slug):
                            env = os.environ.copy()
                            env.pop("CLAUDECODE", None)
                            proc = subprocess.Popen(
                                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, env=env,
                            )
                            output_lines = []
                            for line in proc.stdout:
                                line = line.rstrip()
                                if line.strip():
                                    output_lines.append(line)
                            proc.wait()
                            return proc.returncode, output_lines

                        rc, output = await loop.run_in_executor(None, run_autoship)
                        url = f"https://{slug}.autoship.fun"
                        for line in output:
                            if "LIVE URL:" in line:
                                url = line.split("LIVE URL:")[-1].strip()
                        if rc == 0:
                            await message.channel.send(embed=make_embed(
                                f"Shipped: {slug}", f"Live at {url}",
                                color=0x2ea043, fields=[("URL", url, False)],
                            ))
                        else:
                            last_lines = "\n".join(output[-15:])
                            await message.channel.send(embed=make_embed(
                                f"AutoShip Failed: {slug}",
                                f"```\n{last_lines[-1500:]}\n```", color=0xda3633,
                            ))

                elif action_type == "stop":
                    name = action.get("name", "")
                    if name and builds.stop_build(name):
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{name}**", color=0xda3633,
                        ))

                elif action_type == "stop_all":
                    stopped = builds.stop_all()
                    if stopped:
                        await message.channel.send(embed=make_embed(
                            "All Builds Stopped",
                            "Killed: " + ", ".join(stopped), color=0xda3633,
                        ))

                elif action_type == "tweet":
                    text = action.get("text", "")
                    if text:
                        ok, result = do_tweet(text)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Tweeted" if ok else "Tweet Failed",
                            f"{text}\n\n{result}", color=color,
                        ))

                elif action_type == "thread":
                    tweets = action.get("tweets", [])
                    if tweets:
                        preview = "\n\n".join(f"**{i+1}.** {t}" for i, t in enumerate(tweets))
                        await message.channel.send(embed=make_embed(
                            f"Posting thread ({len(tweets)} tweets)...",
                            preview[:4000], color=0x1da1f2,
                        ))
                        ok, result = do_thread(tweets)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Thread Posted" if ok else "Thread Failed",
                            result[:2000], color=color,
                        ))

                elif action_type == "run":
                    cmd = action.get("command", "")
                    label = action.get("label", "Running command")
                    if cmd:
                        await message.channel.send(embed=make_embed(
                            label, f"```\n{cmd}\n```", color=0xa78bfa,
                        ))
                        try:
                            result = await loop.run_in_executor(
                                None, lambda c=cmd: subprocess.run(
                                    c, shell=True, capture_output=True,
                                    text=True, timeout=60,
                                )
                            )
                            output = (result.stdout + result.stderr).strip()
                            if output:
                                if len(output) > 1800:
                                    output = output[:1800] + "\n... (truncated)"
                                await message.channel.send(f"```\n{output}\n```")
                                run_outputs.append(f"Command: {cmd}\nOutput:\n{output}")
                            else:
                                await message.channel.send("`(no output)`")
                                run_outputs.append(f"Command: {cmd}\nOutput: (no output)")
                        except subprocess.TimeoutExpired:
                            await message.channel.send("`(command timed out)`")
                            run_outputs.append(f"Command: {cmd}\nOutput: (timed out)")
                        except Exception as e:
                            await message.channel.send(f"`Error: {e}`")
                            run_outputs.append(f"Command: {cmd}\nOutput: Error: {e}")

                elif action_type == "reddit":
                    sub = action.get("subreddit", "")
                    title = action.get("title", "")
                    body = action.get("body", "")
                    await message.channel.send(embed=make_embed(
                        f"Reddit Post Draft ({sub})",
                        f"**{title}**\n\n{body[:1500]}",
                        color=0xff4500,
                        fields=[("Note", "Reddit posting requires browser auth. "
                                 "Copy this and post manually.", False)],
                    ))

                elif action_type == "create_tool":
                    name = action.get("name", "")
                    code = action.get("code", "")
                    if name and code:
                        tool_path = TOOLS_DIR / name
                        tool_path.write_text(code)
                        tool_path.chmod(0o755)
                        await message.channel.send(embed=make_embed(
                            f"Tool Created: {name}",
                            f"```\n{code[:1500]}\n```",
                            color=0x2ea043,
                        ))

                elif action_type == "create_skill":
                    name = action.get("name", "")
                    skill_content = action.get("content", "")
                    if name and skill_content:
                        skill_path = SKILLS_DIR / name
                        skill_path.write_text(skill_content)
                        await message.channel.send(embed=make_embed(
                            f"Skill Learned: {name}",
                            f"New skill loaded. I'll remember this for next time.",
                            color=0x2ea043,
                        ))

                elif action_type == "restart":
                    await message.channel.send(embed=make_embed(
                        "Restarting...",
                        "Picking up new skills and code. Back in a sec.",
                        color=0xff9f1c,
                    ))
                    await client.close()
                    os.execv(sys.executable, [sys.executable] + sys.argv)

            # if run commands produced output, feed results back to LLM for follow-up
            if run_outputs:
                results_text = "\n\n".join(run_outputs)
                user_message = (
                    f"PREVIOUS REQUEST: {content}\n\n"
                    f"COMMAND RESULTS:\n{results_text}\n\n"
                    f"Based on these results, complete the user's original request. "
                    f"If you need to take further action (reply to a tweet, like something, etc.), "
                    f"include the appropriate ACTION. If the task is done, just summarize what happened."
                )
                continue  # go back to LLM with results
            else:
                break  # no run outputs, we're done

    client.run(token)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="ground control — talk to autopilot from discord",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
setup:
  1. Go to https://discord.com/developers/applications
  2. Create a new application > Bot > copy token
  3. OAuth2 > URL Generator > select 'bot' scope
     > select 'Send Messages', 'Read Message History' permissions
  4. Open the generated URL to invite bot to your server
  5. Right-click your channel > Copy Channel ID (enable Developer Mode in settings)

run:
  # DM mode (just talk to the bot directly)
  python3 ground_control.py --token YOUR_TOKEN --owner YOUR_DISCORD_USER_ID

  # channel mode (bot listens in a specific channel)
  python3 ground_control.py --token YOUR_TOKEN --channel CHANNEL_ID

  # both (DMs + channel)
  python3 ground_control.py --token YOUR_TOKEN --owner USER_ID --channel CHANNEL_ID

  # env vars work too
  AUTOPILOT_DISCORD_TOKEN=xxx AUTOPILOT_DISCORD_OWNER=123 python3 ground_control.py

how to get your user ID:
  Discord Settings > Advanced > enable Developer Mode
  Click your own profile > Copy User ID
"""
    )
    p.add_argument("--token", type=str,
                   default=os.environ.get("AUTOPILOT_DISCORD_TOKEN"),
                   help="Discord bot token (or set AUTOPILOT_DISCORD_TOKEN)")
    p.add_argument("--channel", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_CHANNEL", "0")) or None,
                   help="Discord channel ID — optional (or set AUTOPILOT_DISCORD_CHANNEL)")
    p.add_argument("--owner", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_OWNER", "0")) or None,
                   help="Your Discord user ID — locks DMs to only you (or set AUTOPILOT_DISCORD_OWNER)")
    p.add_argument("--engine", type=str, default="claude", choices=["claude", "codex"],
                   help="LLM engine for conversational mode (default: claude)")

    args = p.parse_args()

    if not args.token:
        print("Error: provide --token or set AUTOPILOT_DISCORD_TOKEN")
        sys.exit(1)
    if not args.channel and not args.owner:
        print("Error: provide --channel and/or --owner")
        print("  --owner YOUR_ID   → bot responds to your DMs")
        print("  --channel CHAN_ID  → bot responds in a channel")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  ground control")
    print(f"{'='*50}")
    if args.owner:
        print(f"  owner:    {args.owner} (DMs enabled)")
    if args.channel:
        print(f"  channel:  {args.channel}")
    print(f"  engine:   {args.engine}")
    print(f"  autopilot: {AUTOPILOT}")
    print(f"{'='*50}\n")

    create_bot(args.token, args.channel, args.owner, args.engine)
