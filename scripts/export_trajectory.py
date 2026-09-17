"""Export a Claude Code session transcript (.jsonl) to readable markdown.

Usage (from repo root):
    uv run python scripts/export_trajectory.py <session.jsonl> [AGENT_TRAJECTORY.md]

Keeps user messages, the agent's visible text and thinking, every tool call
(command or arguments, truncated) and every tool result (truncated). Drops
harness bookkeeping records.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

TOOL_INPUT_LIMIT = 2500
TOOL_RESULT_LIMIT = 1800


def clip(text: str, limit: int) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [{len(text) - limit} more characters]"


def render_tool_use(block: dict) -> str:
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    if name == "Bash":
        desc = inp.get("description", "")
        body = f"**Tool: Bash** — {desc}\n\n```bash\n{clip(inp.get('command', ''), TOOL_INPUT_LIMIT)}\n```"
    elif name in ("Read", "Write", "Edit"):
        path = inp.get("file_path", "")
        body = f"**Tool: {name}** `{path}`"
        if name == "Write":
            body += f"\n\n```\n{clip(inp.get('content', ''), TOOL_INPUT_LIMIT)}\n```"
        elif name == "Edit":
            body += f"\n\n```diff\n- {clip(inp.get('old_string', ''), 600)}\n+ {clip(inp.get('new_string', ''), 600)}\n```"
    else:
        body = f"**Tool: {name}**\n\n```json\n{clip(json.dumps(inp, indent=1, ensure_ascii=False), TOOL_INPUT_LIMIT)}\n```"
    return body


def render_tool_result(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        text = "\n".join(parts)
    else:
        text = str(content or "")
    tag = "Tool result (error)" if block.get("is_error") else "Tool result"
    return f"*{tag}:*\n\n```\n{clip(text, TOOL_RESULT_LIMIT)}\n```"


def main() -> int:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("AGENT_TRAJECTORY.md")
    lines = [
        "# Agent trajectory",
        "",
        f"Claude Code session transcript, exported {datetime.now().strftime('%Y-%m-%d %H:%M')} from `{src.name}`.",
        "The session was started fresh inside this repository; the first message is the kickoff prompt.",
        "Tool inputs and results are truncated for readability. The export itself and its commit happened after the last entry.",
        "",
        "---",
        "",
    ]
    turn = 0
    for raw in src.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")
        stamp = rec.get("timestamp", "")
        stamp = stamp[11:19] if isinstance(stamp, str) and len(stamp) > 19 else ""

        if isinstance(content, str):
            if role == "user":
                turn += 1
                lines += [f"## Turn {turn} — User {stamp}", "", content.strip(), ""]
            else:
                lines += [f"### Assistant {stamp}", "", content.strip(), ""]
            continue
        if not isinstance(content, list):
            continue

        for block in content:
            kind = block.get("type")
            if kind == "text" and role == "user":
                turn += 1
                lines += [f"## Turn {turn} — User {stamp}", "", block.get("text", "").strip(), ""]
            elif kind == "text":
                lines += [f"### Assistant {stamp}", "", block.get("text", "").strip(), ""]
            elif kind == "thinking":
                lines += ["<details><summary>Assistant thinking</summary>", "", clip(block.get("thinking", ""), 6000), "", "</details>", ""]
            elif kind == "tool_use":
                lines += [render_tool_use(block), ""]
            elif kind == "tool_result":
                lines += [render_tool_result(block), ""]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {turn} user turns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
