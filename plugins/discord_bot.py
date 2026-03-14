#!/usr/bin/env python3
"""autopilot discord plugin — control autopilot from your phone.

Setup:
  1. Create a bot at https://discord.com/developers/applications
  2. Copy the bot token
  3. Invite bot to your server with Send Messages + Read Messages permissions
  4. Run: python3 discord_bot.py --token YOUR_BOT_TOKEN --channel CHANNEL_ID

Commands:
  !build <spec>       — start building from inline spec or attached .md file
  !status             — show current build status
  !stop               — stop the current build
  !logs               — show recent action log
  !help               — show available commands
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
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

# ─── State ───────────────────────────────────────────────────────────────────

current_process = None
current_build = None
build_log_lines = []
build_start_time = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_embed(title, description="", color=0x58a6ff, fields=None):
    embed = discord.Embed(title=title, description=description, color=color,
                          timestamp=datetime.now())
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text="autopilot")
    return embed


def tail_lines(path, n=20):
    """Read last n lines from a file."""
    try:
        lines = Path(path).read_text().strip().splitlines()
        return lines[-n:]
    except Exception:
        return []


def get_strategy(project_name):
    """Read strategy file for a project."""
    path = STRATEGY_DIR / f"{project_name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def format_log_entry(line):
    """Format a JSONL log line for display."""
    try:
        entry = json.loads(line)
        action = entry.get("action", "?")
        ok = "+" if entry.get("ok") else "-"
        detail = entry.get("detail", "")[:80]
        ts = entry.get("timestamp", "")[:19]
        return f"`{ts}` {ok} **{action}** {detail}"
    except Exception:
        return line[:100]


# ─── Build runner ────────────────────────────────────────────────────────────

async def run_build(channel, spec_text, engine="codex", iterations=5, reasoning=None):
    global current_process, current_build, build_log_lines, build_start_time

    # write spec to temp file
    spec_dir = Path.home() / ".autopilot" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"discord-{int(time.time())}.md"
    spec_path.write_text(spec_text)

    project_name = spec_path.stem
    current_build = project_name
    build_log_lines = []
    build_start_time = time.time()

    cmd = [sys.executable, str(AUTOPILOT), str(spec_path),
           "--build", "-e", engine, "--iterations", str(iterations)]
    if reasoning:
        cmd += ["--reasoning", reasoning]

    await channel.send(embed=make_embed(
        "Build Started",
        f"```\n{spec_text[:500]}\n```",
        color=0x2ea043,
        fields=[
            ("Engine", engine, True),
            ("Iterations", str(iterations), True),
            ("Spec", spec_path.name, True),
        ]
    ))

    # run in background thread
    loop = asyncio.get_event_loop()

    def run_process():
        global current_process
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        current_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env,
        )
        for line in current_process.stdout:
            line = line.rstrip()
            if line.strip():
                build_log_lines.append(line)
                # send important lines to discord
                if any(kw in line for kw in ["ITERATION", "Planning", "Building",
                       "Testing", "Pushed", "Tweet", "COMPLETE", "failed", "error"]):
                    asyncio.run_coroutine_threadsafe(
                        channel.send(f"```\n{line[:200]}\n```"),
                        loop
                    )
        current_process.wait()

    thread = threading.Thread(target=run_process, daemon=True)
    thread.start()

    # wait for completion in background
    def wait_done():
        thread.join()
        elapsed = time.time() - build_start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        # read strategy for summary
        strategy = None
        for f in STRATEGY_DIR.iterdir():
            if f.suffix == ".json":
                try:
                    s = json.loads(f.read_text())
                    if s.get("repo_url"):
                        strategy = s
                except Exception:
                    pass

        fields = [("Time", f"{mins}m {secs}s", True)]
        if strategy:
            repo = strategy.get("repo_url", "")
            features = []
            for b in strategy.get("build_history", []):
                features.extend(b.get("features", []))
            if repo:
                fields.append(("Repo", repo, False))
            if features:
                feat_list = "\n".join(f"+ {f}" for f in features[-10:])
                fields.append(("Features", f"```\n{feat_list}\n```", False))

        rc = current_process.returncode if current_process else -1
        color = 0x2ea043 if rc == 0 else 0xda3633

        asyncio.run_coroutine_threadsafe(
            channel.send(embed=make_embed(
                "Build Complete" if rc == 0 else "Build Failed",
                f"Exit code: {rc}",
                color=color,
                fields=fields,
            )),
            loop
        )

        global current_build
        current_build = None
        current_process = None

    threading.Thread(target=wait_done, daemon=True).start()


# ─── Bot ─────────────────────────────────────────────────────────────────────

def create_bot(token, channel_id):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"  autopilot bot online as {client.user}")
        print(f"  watching channel: {channel_id}")
        ch = client.get_channel(channel_id)
        if ch:
            await ch.send(embed=make_embed(
                "AutoPilot Online",
                "Send `!help` to see commands.",
                color=0x58a6ff,
            ))

    @client.event
    async def on_message(message):
        if message.author.bot:
            return
        if channel_id and message.channel.id != channel_id:
            return

        content = message.content.strip()
        if not content.startswith("!"):
            return

        parts = content.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # ── !help ──
        if cmd == "!help":
            await message.channel.send(embed=make_embed(
                "AutoPilot Commands",
                "",
                color=0x58a6ff,
                fields=[
                    ("!build <spec>", "Start a build. Paste spec text or attach a .md file.", False),
                    ("!build-opts", "`!build-opts engine=codex iter=5 reasoning=medium`\nSet defaults for next build.", False),
                    ("!status", "Show current build status.", False),
                    ("!stop", "Stop the current build.", False),
                    ("!logs [project]", "Show recent log entries.", False),
                    ("!projects", "List all built projects.", False),
                ]
            ))

        # ── !build ──
        elif cmd == "!build":
            if current_build:
                await message.channel.send(embed=make_embed(
                    "Build Already Running",
                    f"Currently building: **{current_build}**\nUse `!stop` first.",
                    color=0xda3633,
                ))
                return

            spec_text = args

            # check for attached .md file
            if message.attachments:
                for att in message.attachments:
                    if att.filename.endswith(".md"):
                        spec_text = (await att.read()).decode("utf-8")
                        break

            if not spec_text:
                await message.channel.send("Send a spec with the command or attach a `.md` file.\n"
                                           "Example: `!build A todo app with dark mode and local storage`")
                return

            await run_build(message.channel, spec_text)

        # ── !status ──
        elif cmd == "!status":
            if not current_build:
                await message.channel.send(embed=make_embed(
                    "No Active Build",
                    "Nothing running right now. Use `!build` to start one.",
                    color=0x8b949e,
                ))
                return

            elapsed = time.time() - (build_start_time or time.time())
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)

            recent = build_log_lines[-15:] if build_log_lines else ["(no output yet)"]
            log_text = "\n".join(recent)
            if len(log_text) > 1800:
                log_text = log_text[-1800:]

            await message.channel.send(embed=make_embed(
                f"Building: {current_build}",
                f"```\n{log_text}\n```",
                color=0xff9f1c,
                fields=[("Elapsed", f"{mins}m {secs}s", True)],
            ))

        # ── !stop ──
        elif cmd == "!stop":
            if current_process:
                try:
                    os.kill(current_process.pid, signal.SIGTERM)
                    await message.channel.send(embed=make_embed(
                        "Build Stopped",
                        f"Killed build: **{current_build}**",
                        color=0xda3633,
                    ))
                except Exception as e:
                    await message.channel.send(f"Failed to stop: {e}")
            else:
                await message.channel.send("Nothing running.")

        # ── !logs ──
        elif cmd == "!logs":
            project = args.strip() or current_build
            if not project:
                # list available logs
                logs = list(LOGS_DIR.glob("*.jsonl"))
                if logs:
                    names = "\n".join(f"- `{l.stem}`" for l in sorted(logs)[-10:])
                    await message.channel.send(embed=make_embed(
                        "Available Logs",
                        names,
                        color=0x58a6ff,
                    ))
                else:
                    await message.channel.send("No logs found.")
                return

            log_path = LOGS_DIR / f"{project}.jsonl"
            if not log_path.exists():
                await message.channel.send(f"No log found for `{project}`")
                return

            lines = tail_lines(log_path, 10)
            formatted = "\n".join(format_log_entry(l) for l in lines)
            await message.channel.send(embed=make_embed(
                f"Logs: {project}",
                formatted[:2000],
                color=0x58a6ff,
            ))

        # ── !projects ──
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
                "Projects",
                "\n".join(lines[:15]),
                color=0x58a6ff,
            ))

    client.run(token)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="autopilot discord plugin — control builds from your phone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
setup:
  1. Go to https://discord.com/developers/applications
  2. Create a new application → Bot → copy token
  3. OAuth2 → URL Generator → select 'bot' scope
     → select 'Send Messages', 'Read Message History' permissions
  4. Open the generated URL to invite bot to your server
  5. Right-click your channel → Copy Channel ID (enable Developer Mode in settings)

run:
  python3 discord_bot.py --token YOUR_TOKEN --channel CHANNEL_ID

  # or use env vars
  AUTOPILOT_DISCORD_TOKEN=xxx AUTOPILOT_DISCORD_CHANNEL=123 python3 discord_bot.py
"""
    )
    p.add_argument("--token", type=str,
                   default=os.environ.get("AUTOPILOT_DISCORD_TOKEN"),
                   help="Discord bot token (or set AUTOPILOT_DISCORD_TOKEN)")
    p.add_argument("--channel", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_CHANNEL", "0")),
                   help="Discord channel ID to listen on (or set AUTOPILOT_DISCORD_CHANNEL)")

    args = p.parse_args()

    if not args.token:
        print("Error: provide --token or set AUTOPILOT_DISCORD_TOKEN")
        sys.exit(1)
    if not args.channel:
        print("Error: provide --channel or set AUTOPILOT_DISCORD_CHANNEL")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  autopilot discord plugin")
    print(f"{'='*50}")
    print(f"  channel: {args.channel}")
    print(f"  autopilot: {AUTOPILOT}")
    print(f"{'='*50}\n")

    create_bot(args.token, args.channel)
