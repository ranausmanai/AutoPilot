#!/usr/bin/env python3
"""ground control — talk to autopilot from discord.

Your command center for autopilot. Run parallel builds, check status,
tweet, post to reddit, or just ask what's happening in plain english.

Setup:
  1. Create a bot at https://discord.com/developers/applications
  2. Copy the bot token
  3. Invite bot to your server (Send Messages, Read Message History, Attach Files)
  4. Run: python3 ground_control.py --token YOUR_TOKEN --channel CHANNEL_ID

Commands:
  !build <spec>       — start a build (runs in parallel if others are active)
  !status             — show all active builds
  !stop <name>        — stop a specific build (or !stop all)
  !logs [project]     — show recent action log
  !projects           — list all built projects
  !tweet <text>       — post a tweet
  !help               — show commands

Or just talk naturally:
  "what's it building?"
  "how long has darktick been running?"
  "tweet about the new clock app"
  "find a subreddit for developer tools and post about autoprompt"
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
BUILDS_DIR = Path.home() / ".autopilot" / "builds"
LOGS_DIR = Path.home() / ".autopilot" / "logs"
STRATEGY_DIR = Path.home() / ".autopilot" / "strategy"
SPECS_DIR = Path.home() / ".autopilot" / "specs"

for d in [BUILDS_DIR, LOGS_DIR, STRATEGY_DIR, SPECS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


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

    prompt = f"""You are Ground Control, the discord assistant for AutoPilot (an autonomous build + growth engine).

CURRENT STATE:
{build_summary}

AVAILABLE PROJECTS:
{list_projects_text()}

USER MESSAGE: {user_message}

Respond conversationally in 1-3 sentences. Be concise and helpful. Sound like a teammate, not a bot.

If the user wants you to DO something (start a build, stop a build, tweet, post to reddit, etc.),
include an ACTION block at the end of your response in this exact format:

ACTION: {{"type": "build", "spec": "the spec text", "name": "project-name"}}
ACTION: {{"type": "stop", "name": "project-name"}}
ACTION: {{"type": "stop_all"}}
ACTION: {{"type": "tweet", "text": "the tweet text"}}
ACTION: {{"type": "reddit", "subreddit": "r/example", "title": "post title", "body": "post body"}}
ACTION: {{"type": "discover", "query": "what to search for"}}

Only include ACTION if the user clearly wants something done. For status questions, just answer.
Do NOT use em dashes. Do not be overly enthusiastic. Be direct."""

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
    """Split LLM response into message and optional action."""
    action = None
    message = text

    # extract ACTION line
    match = re.search(r'ACTION:\s*(\{.*\})', text)
    if match:
        message = text[:match.start()].strip()
        try:
            action = json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return message, action


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
            ["twitter", "update", text],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()[:200]
        return False, result.stderr[:200]
    except FileNotFoundError:
        return False, "twitter-cli not installed"
    except Exception as e:
        return False, str(e)


# ─── Bot ─────────────────────────────────────────────────────────────────────

def create_bot(token, channel_id, engine="claude"):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    builds = BuildManager()

    @client.event
    async def on_ready():
        print(f"  ground control online as {client.user}")
        print(f"  watching channel: {channel_id}")
        ch = client.get_channel(channel_id)
        if ch:
            await ch.send(embed=make_embed(
                "Ground Control Online",
                "Talk to me naturally or use `!help` for commands.\n"
                "I can run parallel builds, tweet, post to reddit, and more.",
                color=0x58a6ff,
            ))

    @client.event
    async def on_message(message):
        if message.author.bot:
            return
        if channel_id and message.channel.id != channel_id:
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
                    "Talk naturally or use commands:",
                    color=0x58a6ff,
                    fields=[
                        ("!build <spec>", "Start a build (parallel OK)", False),
                        ("!status", "Show all active builds", False),
                        ("!stop <name>", "Stop a build (!stop all for all)", False),
                        ("!tweet <text>", "Post a tweet", False),
                        ("!logs [project]", "Show recent logs", False),
                        ("!projects", "List all projects", False),
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

            return  # handled command, don't fall through to conversational

        # ── Conversational mode (no ! prefix) ──
        async with message.channel.typing():
            build_summary = builds.get_summary()
            response, action = await loop.run_in_executor(
                None, llm_respond, content, build_summary, engine
            )

        # send the text response
        if response:
            await message.channel.send(response[:2000])

        # execute action if any
        if action:
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
                        "Killed: " + ", ".join(stopped),
                        color=0xda3633,
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

            elif action_type == "reddit":
                sub = action.get("subreddit", "")
                title = action.get("title", "")
                body = action.get("body", "")
                await message.channel.send(embed=make_embed(
                    f"Reddit Post Draft ({sub})",
                    f"**{title}**\n\n{body[:1500]}",
                    color=0xff4500,
                    fields=[("Note", "Reddit posting requires browser auth. "
                             "Copy this and post manually, or set up autopilot's reddit integration.", False)],
                ))

            elif action_type == "discover":
                query = action.get("query", "")
                await message.channel.send(embed=make_embed(
                    "Discovering communities...",
                    f"Searching for: {query}",
                    color=0xa78bfa,
                ))
                # run discovery through autopilot's LLM
                disc_result = await loop.run_in_executor(
                    None, lambda: llm_respond(
                        f"Find 5-10 online communities where I can share: {query}. "
                        f"List them with platform, name, and why they'd be good.",
                        "", engine
                    )
                )
                if disc_result[0]:
                    await message.channel.send(disc_result[0][:2000])

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
  python3 ground_control.py --token YOUR_TOKEN --channel CHANNEL_ID

  # or use env vars
  AUTOPILOT_DISCORD_TOKEN=xxx AUTOPILOT_DISCORD_CHANNEL=123 python3 ground_control.py

  # use codex instead of claude for conversational responses
  python3 ground_control.py --token TOKEN --channel ID --engine codex
"""
    )
    p.add_argument("--token", type=str,
                   default=os.environ.get("AUTOPILOT_DISCORD_TOKEN"),
                   help="Discord bot token (or set AUTOPILOT_DISCORD_TOKEN)")
    p.add_argument("--channel", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_CHANNEL", "0")),
                   help="Discord channel ID (or set AUTOPILOT_DISCORD_CHANNEL)")
    p.add_argument("--engine", type=str, default="claude", choices=["claude", "codex"],
                   help="LLM engine for conversational mode (default: claude)")

    args = p.parse_args()

    if not args.token:
        print("Error: provide --token or set AUTOPILOT_DISCORD_TOKEN")
        sys.exit(1)
    if not args.channel:
        print("Error: provide --channel or set AUTOPILOT_DISCORD_CHANNEL")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  ground control")
    print(f"{'='*50}")
    print(f"  channel:  {args.channel}")
    print(f"  engine:   {args.engine}")
    print(f"  autopilot: {AUTOPILOT}")
    print(f"{'='*50}\n")

    create_bot(args.token, args.channel, args.engine)
