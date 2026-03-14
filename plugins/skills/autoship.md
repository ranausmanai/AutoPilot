# AutoShip — spec to deployed app

AutoShip builds and deploys apps from a markdown spec. It lives at `autoship/autoship.py`.
Apps deploy to `https://<slug>.autoship.fun` automatically.

## Build & Deploy (one command)

```bash
# build + deploy to autoship.fun
python3 autoship/autoship.py SPEC_FILE -e claude --deploy autoship --slug my-app

# build only (no deploy)
python3 autoship/autoship.py SPEC_FILE -e claude

# build with codex engine
python3 autoship/autoship.py SPEC_FILE -e codex --deploy autoship --slug my-app

# custom output directory
python3 autoship/autoship.py SPEC_FILE -e claude -o my-app-dir --deploy autoship
```

## Update an existing app

Write a change request markdown file and point it at the existing output dir:

```bash
# update and redeploy
python3 autoship/autoship.py changes.md -o existing-app-dir --deploy autoship
```

## Auth / Login

Before deploying, the user must be logged in:

```bash
# login with invite code
python3 autoship/autoship.py login --code SHIP-ABC123

# or use env var
export AUTOSHIP_API_TOKEN=your-token
```

Auth is saved to `~/.config/autoship/auth.json`.

## How it works

1. Reads the spec markdown
2. Plans the build (app type, stack, files, capabilities)
3. Runs an AI agent (claude or codex) to write all the code
4. Verifies all planned files exist, retries up to 3 times if not
5. If `--deploy autoship`: ensures Dockerfile + autoship.json exist, uploads bundle to api.autoship.fun, deploys to Docker + Nginx with auto SSL

## What gets deployed

- Apps run in Docker containers on the autoship.fun VPS
- Each app gets `https://<slug>.autoship.fun` with Let's Encrypt SSL
- Persistent data lives under `/data` in the container
- SQLite databases go to `/data/app.db`
- Apps must listen on `0.0.0.0:$PORT` (default 8000)

## Spec format

The spec is just a markdown file describing what you want:

```markdown
# My App

A simple todo app with:
- Dark mode
- Local storage persistence
- Drag and drop reordering
- Categories/tags for tasks
```

## Tips

- The slug becomes the subdomain: `--slug cool-app` -> `cool-app.autoship.fun`
- If no slug is given, it's derived from the output directory name
- Specs can be as simple as one sentence or as detailed as a full PRD
- For the SPEC_FILE, write the spec to a temp .md file first, then pass the path
- The autoship.py script is at: autoship/autoship.py (relative to the project root)
