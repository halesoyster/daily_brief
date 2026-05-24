"""
daily_brief — daily HTML brief synthesizer for any code project.

Claude reads the project, picks one part of the codebase to walk through,
synthesizes what shipped in the last 24h, and (optionally) emails the
whole thing. Interview prep disguised as a morning standup.

Structure per brief (~60/25/15):
  - Spotlight (60%): one repo area per day, tools used and why those over
    alternatives, design decisions, annotated code excerpts, a "how to
    explain this in 30 seconds" framing.
  - What shipped (25%): git activity in the last 24 hours, translated to
    technical-but-readable language.
  - What's next (15%): brief orientation toward the next concrete piece.

Rotation: spotlight picked from files touched in the last 24h, weighted
against the coverage log (.brief/coverage.json) to avoid rehash. Falls
back to a curated curriculum list when no fresh changes warrant a spotlight.

Run:
  daily-brief                                    # current directory
  daily-brief --project ~/Projects/moon_baby     # specific project
  daily-brief --dry-run                          # stdout, no save
  daily-brief --no-open                          # save, no browser
  daily-brief --email                            # also send via SMTP

Cost: roughly one Claude API call per run. Sonnet 4.6, ~$0.10/day.

Per-project behavior is configured via a .daily-brief.yaml file in the
project root. If none is found, a generic fallback is used. See README
for the full schema.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import anthropic
import yaml

# ---------------------------------------------------------------------------
# Project configurations
# ---------------------------------------------------------------------------
# load_project_config() reads .daily-brief.yaml from the project root.
# GENERIC_CONFIG is the in-code fallback for projects with no config file.
# Schema documented in README.md.

GENERIC_CONFIG: dict[str, Any] = {
    "name": "project",
    "file_extensions": (
        ".py", ".md", ".sql",
        ".ts", ".tsx", ".js", ".jsx",
        ".go", ".rs", ".rb", ".java", ".kt",
        ".sh", ".html", ".css",
    ),
    "sprint_state_files": [],
    "roadmap_file": None,
    "audience_description": "the developer who owns this project",
    "goal_description": "a daily technical walk-through of one part of the codebase",
    "curriculum": [],
}


def load_project_config(project_root: Path) -> dict[str, Any]:
    """Load .daily-brief.yaml from project_root; fall back to GENERIC_CONFIG."""
    yaml_path = project_root / ".daily-brief.yaml"
    if yaml_path.exists():
        try:
            with yaml_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            # YAML loads sequences as lists; file_extensions must be a tuple
            # for str.endswith() compatibility.
            if "file_extensions" in data:
                data["file_extensions"] = tuple(data["file_extensions"])
            return data
        except Exception as exc:  # noqa: BLE001
            print(f"[daily_brief] warning: could not load {yaml_path}: {exc}", flush=True)
    return dict(GENERIC_CONFIG)


# ---------------------------------------------------------------------------
# Runtime paths (set in main() once we know the project root)
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path.cwd()
BRIEF_DIR: Path = REPO_ROOT / ".brief"
COVERAGE_FILE: Path = BRIEF_DIR / "coverage.json"
TODAY = datetime.now().date()


def _init_paths(project_root: Path) -> None:
    """Populate the module-level path globals from --project arg."""
    global REPO_ROOT, BRIEF_DIR, COVERAGE_FILE
    REPO_ROOT = project_root.resolve()
    BRIEF_DIR = REPO_ROOT / ".brief"
    COVERAGE_FILE = BRIEF_DIR / "coverage.json"


# ---------------------------------------------------------------------------
# Claude API + system prompt
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000

SYSTEM_PROMPT_TEMPLATE = """You are the project's senior engineer writing a daily technical brief.

The brief is for: {audience}.

The brief's purpose: {goal}.

Your voice:
- Senior engineer explaining to a sharp colleague who's new to this corner of the code.
- Substance over polish. No marketing language. No "exciting" or "robust" or "leverage."
- Tools/libraries get named, and you say WHY those over alternatives. The "vs alternatives" framing is the highest-value part of the brief.
- Trade-offs are named explicitly, not smoothed over.

Output format:
- Return ONLY valid JSON matching the schema below. No prose before or after.
- Code excerpts should be short — 5 to 15 lines max, focused on the interesting part.
- "how_to_explain" is a 30-to-60 second verbal walkthrough the reader could give to someone unfamiliar with the area.

Schema:
{{
  "headline": "string — one sentence framing today's brief",
  "spotlight": {{
    "title": "string — the area being spotlighted",
    "files_covered": ["string — file paths"],
    "tldr": "string — 1-2 sentences on what this area does",
    "what_it_does": "string — 2-4 paragraphs, prose, technical-but-readable",
    "tools_used": [
      {{
        "name": "string — library or pattern name",
        "why_this": "string — why this choice was made for this project specifically",
        "alternatives": "string — what else could have been used and the trade-off"
      }}
    ],
    "design_decisions": [
      "string — one design decision baked in, with its reasoning"
    ],
    "code_excerpts": [
      {{
        "path": "string — file path",
        "language": "string — python | sql | typescript | etc",
        "snippet": "string — the actual code, 5-15 lines",
        "note": "string — what to notice in this snippet"
      }}
    ],
    "how_to_explain": "string — a verbal walkthrough the reader could give in 30-60 seconds spoken"
  }},
  "what_shipped": [
    {{
      "title": "string — short title for this change",
      "technical_summary": "string — 1-3 sentences, technical-but-readable",
      "files": ["string — file paths touched"]
    }}
  ],
  "whats_next": "string — 2-4 sentences on the next concrete piece of work and why it matters"
}}
"""


USER_PROMPT_TEMPLATE = """## Today's spotlight
{spotlight_label}
Files: {spotlight_files}

## Spotlight file contents
{spotlight_contents}

## Coverage history (do NOT repeat these angles)
{coverage_history}

## Git activity — last 24 hours
{git_24h}

## Git activity — last 7 days (context)
{git_7d}

## Project state — current
{sprint_state}

## Roadmap snippet
{roadmap_snippet}

---

Write today's brief. Return only the JSON object."""


# ---------------------------------------------------------------------------
# State gathering
# ---------------------------------------------------------------------------

@dataclass
class State:
    git_24h: str
    git_7d: str
    changed_files_24h: list[str]
    sprint_state: str
    roadmap_snippet: str
    coverage_log: list[dict[str, Any]] = field(default_factory=list)


def run_git(args: list[str]) -> str:
    """Run a git command from the project root, return stdout. Empty string on error."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def git_log_window(since: str, until: str | None = None) -> str:
    """Return a formatted git log for the given window."""
    args = [
        "log",
        f"--since={since}",
        "--pretty=format:%h %ad %s%n  files: %ae",
        "--date=short",
        "--name-only",
    ]
    if until:
        args.append(f"--until={until}")
    return run_git(args)


def git_changed_files(since: str) -> list[str]:
    """Return unique file paths changed in the given window."""
    raw = run_git([
        "log",
        f"--since={since}",
        "--name-only",
        "--pretty=format:",
    ])
    files = sorted({line.strip() for line in raw.splitlines() if line.strip()})
    return files


def read_file_truncated(path: Path, max_lines: int = 200) -> str:
    """Read a file, truncate to max_lines, note if truncated."""
    if not path.exists():
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
        return f"(file not found: {rel})"
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return f"(unreadable: {exc})"
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = "\n".join(lines[:max_lines])
    return f"{head}\n\n... [truncated; {len(lines) - max_lines} more lines]"


def gather_sprint_state(config: dict[str, Any]) -> str:
    """Read the project's sprint-state files, if any are configured."""
    parts: list[str] = []
    for rel_path in config.get("sprint_state_files", []):
        path = REPO_ROOT / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # If the file uses "## YYYY-..." section headers, take just the latest.
        sections = text.split("\n## ")
        if len(sections) >= 2:
            latest = "## " + sections[1]
            if "\n## " in latest:
                latest = latest.split("\n## ")[0]
            parts.append(f"Latest entry from {rel_path}:\n{latest[:3000]}")
        else:
            # Otherwise, just include the head of the file.
            parts.append(f"From {rel_path} (head):\n{text[:2000]}")
    return "\n\n".join(parts) if parts else "(no sprint state configured for this project)"


def gather_roadmap_snippet(config: dict[str, Any]) -> str:
    """Read a concise roadmap excerpt, if a roadmap is configured."""
    roadmap_rel = config.get("roadmap_file")
    if not roadmap_rel:
        return "(no roadmap configured for this project)"
    roadmap = REPO_ROOT / roadmap_rel
    if not roadmap.exists():
        return f"(roadmap file {roadmap_rel} not found)"
    text = roadmap.read_text(encoding="utf-8")
    # Prefer an "Open Engineering Work" section if present (moon_baby convention).
    if "## Open Engineering Work by Stage" in text:
        section = text.split("## Open Engineering Work by Stage")[1].split("\n## ")[0]
        return "## Open Engineering Work by Stage" + section[:2500]
    return text[:2500]


def load_coverage_log() -> list[dict[str, Any]]:
    """Read .brief/coverage.json. Returns [] if missing or malformed."""
    if not COVERAGE_FILE.exists():
        return []
    try:
        return json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_coverage_log(log: list[dict[str, Any]]) -> None:
    """Persist the coverage log."""
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    COVERAGE_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Spotlight selection
# ---------------------------------------------------------------------------

def pick_spotlight(
    changed_files: list[str],
    coverage_log: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Choose today's spotlight.

    Strategy:
      1. If files changed in the last 24h, group them by directory and pick
         the group that was least recently spotlighted (or never).
      2. If nothing changed, pick the least-recently-spotlighted item from
         the project's curriculum (if any). If no curriculum, fall back to
         a "recent changes" placeholder.
    """
    file_extensions = tuple(config.get("file_extensions", ()))
    curriculum = config.get("curriculum", [])

    # Files spotlighted by date (most recent first)
    recently_covered: dict[str, str] = {}
    for entry in coverage_log:
        for f in entry.get("files_covered", []):
            if f not in recently_covered:
                recently_covered[f] = entry["date"]

    # Strategy 1: recent changes
    if changed_files:
        spotlightable = [
            f for f in changed_files
            if (
                f.endswith(file_extensions)
                and not f.startswith(".brief/")
                and not f.endswith("/__init__.py")
            )
        ]
        if spotlightable:
            three_days_ago = (TODAY - timedelta(days=3)).isoformat()
            by_dir: dict[str, list[str]] = {}
            for f in spotlightable:
                parent = str(Path(f).parent)
                by_dir.setdefault(parent, []).append(f)

            def group_freshness(dir_name: str) -> tuple[int, str]:
                files = by_dir[dir_name]
                last_covered = min(
                    (recently_covered.get(f, "0000-00-00") for f in files),
                    default="0000-00-00",
                )
                if last_covered < three_days_ago:
                    return (0, last_covered)
                return (1, last_covered)

            best_dir = min(by_dir.keys(), key=group_freshness)
            files = by_dir[best_dir]
            return {
                "id": f"recent_{best_dir.replace('/', '_')}",
                "label": f"Recent changes in {best_dir}/",
                "files": files,
                "source": "recent_changes",
            }

    # Strategy 2: curriculum fallback (only if the project has one)
    if curriculum:
        def curriculum_freshness(item: dict[str, Any]) -> str:
            files = item["files"]
            last_covered = min(
                (recently_covered.get(f, "0000-00-00") for f in files),
                default="0000-00-00",
            )
            return last_covered

        best_item = min(curriculum, key=curriculum_freshness)
        return {
            "id": best_item["id"],
            "label": best_item["label"],
            "files": best_item["files"],
            "source": "curriculum",
        }

    # Strategy 3: nothing to spotlight (no recent changes, no curriculum)
    return {
        "id": "no_spotlight",
        "label": "No spotlight today — no recent changes and no curriculum configured",
        "files": [],
        "source": "empty",
    }


def load_spotlight_contents(spotlight: dict[str, Any]) -> str:
    """Read each file in the spotlight, concatenated with separators."""
    if not spotlight["files"]:
        return "(no files to spotlight today)"
    parts: list[str] = []
    for rel_path in spotlight["files"]:
        path = REPO_ROOT / rel_path
        content = read_file_truncated(path, max_lines=300)
        parts.append(f"### {rel_path}\n\n```\n{content}\n```")
    return "\n\n".join(parts)


def format_coverage_history(coverage_log: list[dict[str, Any]]) -> str:
    """Summarize recent coverage so Claude doesn't repeat itself."""
    if not coverage_log:
        return "(no prior briefs)"
    recent = coverage_log[-7:]
    lines = []
    for entry in reversed(recent):
        date = entry.get("date", "?")
        label = entry.get("label", "?")
        lines.append(f"  {date}: {label}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def call_claude(state: State, spotlight: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Send the prompt to Claude and parse the JSON response."""
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit(
            "✗ CLAUDE_API_KEY (or ANTHROPIC_API_KEY) not set.\n"
            "  Export it in your shell, or source a loader that does."
        )

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        audience=config["audience_description"],
        goal=config["goal_description"],
    )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        spotlight_label=spotlight["label"],
        spotlight_files=", ".join(spotlight["files"]) if spotlight["files"] else "(none)",
        spotlight_contents=load_spotlight_contents(spotlight),
        coverage_history=format_coverage_history(state.coverage_log),
        git_24h=state.git_24h or "(no commits in last 24h)",
        git_7d=state.git_7d or "(no commits in last 7d)",
        sprint_state=state.sprint_state,
        roadmap_snippet=state.roadmap_snippet,
    )

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[: -3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        debug_path = BRIEF_DIR / f"{TODAY}-raw.txt"
        BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        raise SystemExit(
            f"✗ Claude returned unparseable JSON. Raw output saved to {debug_path}\n"
            f"  JSONDecodeError: {exc}"
        )


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def render_html(brief: dict[str, Any], spotlight: dict[str, Any], state: State, config: dict[str, Any]) -> str:
    """Template the brief into a styled HTML document."""
    spotlight_data = brief.get("spotlight", {})
    project_name = config.get("name", "project")

    tools_html = ""
    for tool in spotlight_data.get("tools_used", []):
        tools_html += f"""
        <div class="tool">
          <div class="tool-name">{escape_html(tool.get('name', ''))}</div>
          <div class="tool-why"><strong>Why this:</strong> {escape_html(tool.get('why_this', ''))}</div>
          <div class="tool-alt"><strong>Alternatives:</strong> {escape_html(tool.get('alternatives', ''))}</div>
        </div>
        """

    decisions_html = "".join(
        f"<li>{escape_html(d)}</li>"
        for d in spotlight_data.get("design_decisions", [])
    )

    excerpts_html = ""
    for excerpt in spotlight_data.get("code_excerpts", []):
        excerpts_html += f"""
        <div class="excerpt">
          <div class="excerpt-path">{escape_html(excerpt.get('path', ''))}</div>
          <pre><code class="lang-{escape_html(excerpt.get('language', 'text'))}">{escape_html(excerpt.get('snippet', ''))}</code></pre>
          <div class="excerpt-note">{escape_html(excerpt.get('note', ''))}</div>
        </div>
        """

    shipped_html = ""
    for item in brief.get("what_shipped", []):
        files = ", ".join(item.get("files", []))
        shipped_html += f"""
        <li>
          <div class="shipped-title">{escape_html(item.get('title', ''))}</div>
          <div class="shipped-summary">{escape_html(item.get('technical_summary', ''))}</div>
          <div class="shipped-files">{escape_html(files)}</div>
        </li>
        """

    sparkline = render_commit_sparkline()

    files_covered = ", ".join(spotlight_data.get("files_covered", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{escape_html(project_name)} — daily brief {TODAY}</title>
<style>
  :root {{
    --bg: #fafaf8;
    --fg: #1a1a1a;
    --muted: #6b6b6b;
    --accent: #2d4a7d;
    --code-bg: #f3f1ec;
    --rule: #e3e0d8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
    line-height: 1.55;
    max-width: 780px;
    margin: 0 auto;
    padding: 40px 24px 80px;
  }}
  header {{ border-bottom: 1px solid var(--rule); padding-bottom: 20px; margin-bottom: 32px; }}
  .meta {{ color: var(--muted); font-size: 13px; letter-spacing: 0.04em; text-transform: uppercase; }}
  h1 {{ font-size: 28px; font-weight: 600; margin: 6px 0 4px; }}
  .headline {{ font-size: 17px; color: var(--muted); margin-top: 8px; }}
  h2 {{ font-size: 22px; font-weight: 600; margin: 40px 0 8px; color: var(--accent); }}
  h3 {{ font-size: 16px; font-weight: 600; margin: 24px 0 8px; }}
  .section-meta {{ color: var(--muted); font-size: 13px; margin-bottom: 16px; }}
  .tldr {{ background: var(--code-bg); border-left: 3px solid var(--accent); padding: 12px 16px; margin: 16px 0; font-style: italic; }}
  .tool {{ background: var(--code-bg); padding: 12px 16px; margin: 12px 0; border-radius: 4px; }}
  .tool-name {{ font-weight: 600; font-size: 15px; color: var(--accent); margin-bottom: 6px; }}
  .tool-why, .tool-alt {{ font-size: 14px; margin: 4px 0; }}
  ul.decisions {{ padding-left: 22px; }}
  ul.decisions li {{ margin: 6px 0; }}
  .excerpt {{ margin: 20px 0; }}
  .excerpt-path {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; color: var(--accent); margin-bottom: 6px; }}
  .excerpt-note {{ font-size: 14px; color: var(--muted); margin-top: 6px; font-style: italic; }}
  pre {{ background: var(--code-bg); padding: 14px 16px; border-radius: 4px; overflow-x: auto; margin: 8px 0; }}
  code {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; line-height: 1.5; }}
  .explain {{ background: #fff8e6; border-left: 3px solid #c89c2a; padding: 14px 18px; margin: 20px 0; border-radius: 0 4px 4px 0; }}
  .explain-label {{ font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase; color: #876418; font-weight: 600; margin-bottom: 6px; }}
  ul.shipped {{ list-style: none; padding: 0; }}
  ul.shipped li {{ margin: 14px 0; padding: 12px 14px; background: var(--code-bg); border-radius: 4px; }}
  .shipped-title {{ font-weight: 600; }}
  .shipped-summary {{ font-size: 14px; margin: 4px 0; }}
  .shipped-files {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; color: var(--muted); }}
  .sparkline {{ margin: 16px 0; }}
  .next {{ margin-top: 32px; padding: 16px 18px; background: #f0eee8; border-radius: 4px; }}
  footer {{ margin-top: 60px; padding-top: 20px; border-top: 1px solid var(--rule); color: var(--muted); font-size: 12px; }}
  footer a {{ color: var(--accent); }}
</style>
</head>
<body>

<header>
  <div class="meta">{escape_html(project_name)} · daily brief · {TODAY.strftime('%A, %B %-d, %Y')}</div>
  <h1>{escape_html(brief.get('headline', 'Today\'s brief'))}</h1>
</header>

<section>
  <h2>Today's spotlight</h2>
  <div class="section-meta">{escape_html(spotlight_data.get('title', spotlight['label']))} · {escape_html(files_covered)} · picked from {spotlight['source'].replace('_', ' ')}</div>

  <div class="tldr">{escape_html(spotlight_data.get('tldr', ''))}</div>

  <h3>What it does</h3>
  <div class="prose">{render_paragraphs(spotlight_data.get('what_it_does', ''))}</div>

  <h3>Tools used (and why those over alternatives)</h3>
  {tools_html}

  <h3>Design decisions baked in</h3>
  <ul class="decisions">{decisions_html}</ul>

  <h3>Code worth reading</h3>
  {excerpts_html}

  <div class="explain">
    <div class="explain-label">How to explain this — 30 seconds out loud</div>
    {escape_html(spotlight_data.get('how_to_explain', ''))}
  </div>
</section>

<section>
  <h2>What shipped — last 24 hours</h2>
  <div class="sparkline">{sparkline}</div>
  <ul class="shipped">{shipped_html if shipped_html else '<li><em>No commits in the last 24 hours.</em></li>'}</ul>
</section>

<section>
  <h2>What's next</h2>
  <div class="next">{escape_html(brief.get('whats_next', ''))}</div>
</section>

<footer>
  Generated by <code>daily-brief</code> · spotlight rotation source: {spotlight['source']}<br>
  Project root: <code>{escape_html(str(REPO_ROOT))}</code>
</footer>

</body>
</html>
"""


def render_paragraphs(text: str) -> str:
    """Split prose by double-newlines into <p> tags. Escape HTML."""
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{escape_html(p)}</p>" for p in paragraphs)


def render_commit_sparkline() -> str:
    """A tiny inline SVG showing commits per day for the last 14 days."""
    log = run_git([
        "log",
        "--since=14 days ago",
        "--pretty=format:%ad",
        "--date=short",
    ])
    if not log:
        return ""
    counts: dict[str, int] = {}
    for line in log.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1

    days = []
    for i in range(13, -1, -1):
        d = (TODAY - timedelta(days=i)).isoformat()
        days.append((d, counts.get(d, 0)))

    if not any(c for _, c in days):
        return '<div style="color: var(--muted); font-size: 13px;">No commits in the last 14 days.</div>'

    max_count = max(c for _, c in days) or 1
    bar_w = 14
    gap = 4
    chart_h = 40
    width = len(days) * (bar_w + gap)
    bars = []
    for i, (d, c) in enumerate(days):
        h = (c / max_count) * chart_h if c else 1
        x = i * (bar_w + gap)
        y = chart_h - h
        color = "#2d4a7d" if c else "#d8d4ca"
        bars.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" fill="{color}"><title>{d}: {c} commit{"s" if c != 1 else ""}</title></rect>')

    today_count = days[-1][1]
    label = f"{sum(c for _, c in days)} commits over 14 days · {today_count} today"

    return f"""
    <svg width="{width}" height="{chart_h}" style="display: block;">{''.join(bars)}</svg>
    <div style="color: var(--muted); font-size: 12px; margin-top: 4px;">{label}</div>
    """


def escape_html(text: str) -> str:
    """Minimal HTML escape."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------

def _plain_text_fallback(brief: dict[str, Any], spotlight: dict[str, Any], config: dict[str, Any]) -> str:
    """A minimal text/plain alternative for email clients that won't render HTML."""
    spot = brief.get("spotlight", {})
    lines = [
        f"{config.get('name', 'project')} daily brief — {TODAY.isoformat()}",
        "",
        brief.get("headline", ""),
        "",
        f"Spotlight: {spot.get('title', spotlight['label'])}",
        spot.get("tldr", ""),
        "",
        "(Open the HTML version for the full brief — your email client should render it inline.)",
    ]
    return "\n".join(line for line in lines if line is not None)


def send_email(html: str, brief: dict[str, Any], spotlight: dict[str, Any], config: dict[str, Any]) -> bool:
    """Send the brief via Gmail SMTP. Returns True on success, False if skipped/failed.

    All three env vars must be present; missing config is a no-op, not an error,
    so a scheduled run still succeeds with the HTML saved locally.
    """
    sender = os.environ.get("BRIEF_EMAIL_FROM", "").strip()
    recipient = os.environ.get("BRIEF_EMAIL_TO", "").strip()
    password = os.environ.get("BRIEF_EMAIL_APP_PASSWORD", "").strip()

    if not (sender and recipient and password):
        print("[daily_brief] email skipped (BRIEF_EMAIL_FROM/_TO/_APP_PASSWORD not all set)")
        return False

    spot = brief.get("spotlight", {})
    spot_title = spot.get("title") or spotlight["label"]
    subject = f"{config.get('name', 'project')} brief · {TODAY.isoformat()} · {spot_title}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(_plain_text_fallback(brief, spotlight, config))
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(sender, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"[daily_brief] ✗ email send failed: {exc}")
        return False

    print(f"[daily_brief] emailed to {recipient}")
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def gather_state(config: dict[str, Any]) -> State:
    coverage_log = load_coverage_log()
    return State(
        git_24h=git_log_window("24 hours ago"),
        git_7d=git_log_window("7 days ago", until="24 hours ago"),
        changed_files_24h=git_changed_files("24 hours ago"),
        sprint_state=gather_sprint_state(config),
        roadmap_snippet=gather_roadmap_snippet(config),
        coverage_log=coverage_log,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a daily HTML brief for a code project.")
    parser.add_argument("--project", type=Path, default=Path.cwd(),
                        help="Project root to brief on. Defaults to current working directory.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print HTML to stdout instead of saving.")
    parser.add_argument("--no-open", action="store_true",
                        help="Save the HTML but do not open it in the browser.")
    parser.add_argument("--email", action="store_true",
                        help="Also send the brief via Gmail SMTP. Requires BRIEF_EMAIL_FROM/_TO/_APP_PASSWORD.")
    args = parser.parse_args()

    if not args.project.exists():
        print(f"✗ Project root does not exist: {args.project}", file=sys.stderr)
        return 1
    if not (args.project / ".git").exists():
        print(f"✗ Not a git repo: {args.project}", file=sys.stderr)
        return 1

    _init_paths(args.project)
    config = load_project_config(REPO_ROOT)

    print(f"[daily_brief] project: {REPO_ROOT}")
    print(f"[daily_brief] config: {config.get('name', 'generic')}")
    print(f"[daily_brief] gathering state…")
    state = gather_state(config)

    print(f"[daily_brief] picking spotlight (from {len(state.changed_files_24h)} files changed in last 24h)…")
    spotlight = pick_spotlight(state.changed_files_24h, state.coverage_log, config)
    print(f"[daily_brief] spotlight: {spotlight['label']} (source: {spotlight['source']})")

    print(f"[daily_brief] calling Claude ({CLAUDE_MODEL})…")
    brief = call_claude(state, spotlight, config)

    print(f"[daily_brief] rendering HTML…")
    html = render_html(brief, spotlight, state, config)

    if args.dry_run:
        print(html)
        return 0

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BRIEF_DIR / f"{TODAY}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[daily_brief] wrote {out_path}")

    state.coverage_log.append({
        "date": TODAY.isoformat(),
        "spotlight_id": spotlight["id"],
        "label": spotlight["label"],
        "files_covered": brief.get("spotlight", {}).get("files_covered", spotlight["files"]),
        "source": spotlight["source"],
    })
    save_coverage_log(state.coverage_log)

    if args.email:
        send_email(html, brief, spotlight, config)

    if not args.no_open:
        subprocess.run(["open", str(out_path)], check=False)
        print(f"[daily_brief] opened in default browser")

    return 0


if __name__ == "__main__":
    sys.exit(main())
