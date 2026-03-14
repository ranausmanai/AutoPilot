# GitPulse

A terminal dashboard that shows your GitHub activity at a glance.

## What it does

- Shows recent PRs (open, merged, review requested)
- Shows recent issues (open, assigned to you)
- Shows contribution streak (days in a row with commits)
- Shows notification count
- All data comes from `gh` CLI (no API tokens needed)
- Refreshes on demand or with `--watch` flag

## Tech

- Single Python file
- No dependencies beyond stdlib + `gh` CLI
- Colored terminal output using ANSI codes
- Clean, compact layout that fits in a tmux pane
