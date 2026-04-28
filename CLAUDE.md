# CLAUDE.md — Project-level instructions for Claude Code

## Environment constraints

**Do NOT run Python or `uv` commands directly.**

This project runs on Windows (via WSL2). The Python environment is managed with `uv`, but Claude Code cannot execute `uv` commands in this shell. Instead:

1. Ask the user to run the command themselves (e.g., `! uv run pytest`)
2. Wait for them to paste the output back into the conversation
3. Continue from there

This applies to any command that would invoke `uv`, `uv run`, `uv sync`, `uv add`, `python`, etc.
