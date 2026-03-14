# Meta — self-improvement and skill creation

You can create new skills and tools at runtime. If a user asks you to do something
you don't have a skill for, build one on the spot.

## When to create a new skill

- The user asks for something no existing skill covers
- You find yourself writing the same curl/jq pattern repeatedly
- A multi-step workflow could be simplified into a reusable script

## How to create a tool (script)

Write a Python or shell script to `plugins/tools/` using the `create_tool` action:

ACTION: {"type": "create_tool", "name": "devto_post.py", "code": "#!/usr/bin/env python3\n..."}

The script will be saved to `plugins/tools/devto_post.py` and made executable.

Rules for tools:
- Use only stdlib Python or basic shell (curl, jq, grep, etc.)
- Accept arguments via CLI args or stdin
- Print output to stdout (JSON preferred for parseability)
- Include a usage comment at the top
- Handle errors gracefully, print to stderr
- Keep scripts short and focused (under 100 lines)

## How to create a skill (documentation)

Write a markdown skill file to `plugins/skills/` using the `create_skill` action:

ACTION: {"type": "create_skill", "name": "devto.md", "content": "# Dev.to\n\nHow to use the devto_post.py tool...\n"}

The skill will be loaded automatically on the next message.

Rules for skills:
- Document the commands/tools with examples
- Include common use cases
- Add tips and gotchas
- Reference any tools you created in plugins/tools/

## Example: creating a Dev.to integration

Step 1 — create the tool:
ACTION: {"type": "create_tool", "name": "devto.py", "code": "#!/usr/bin/env python3\n\"\"\"Post articles to Dev.to via API.\n\nUsage:\n  python3 devto.py post --title 'Title' --body 'markdown content' --tags python,ai\n  python3 devto.py articles --username USERNAME\n\"\"\"\nimport argparse, json, os, urllib.request\n...\n"}

Step 2 — create the skill:
ACTION: {"type": "create_skill", "name": "devto.md", "content": "# Dev.to\n\nPost and manage articles on Dev.to.\n\n## Post an article\n```bash\npython3 plugins/tools/devto.py post --title 'Title' --body 'content' --tags ai,python\n```\n...\n"}

Step 3 — use it immediately:
ACTION: {"type": "run", "command": "python3 plugins/tools/devto.py post --title 'My Post' --body 'content'", "label": "Posting to Dev.to"}

## Principles

- Build tools that are REUSABLE, not one-off scripts
- Keep tools small and composable
- Always create both a tool AND a skill file
- Test the tool with a run action before telling the user it works
- If a tool needs an API key, document how to set it as an env var
- Never hardcode credentials in tools
