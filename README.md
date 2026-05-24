# daily_brief

> A daily HTML brief synthesizer for any code project. Claude reads what shipped, picks one part of the codebase to walk you through, explains the engineering choices, and emails the whole thing to your inbox at 9am.

Interview prep disguised as a morning standup.

---

## What it does

Each morning, `daily-brief` against a project produces a self-contained HTML brief with three sections:

- **Spotlight (60%)** — one corner of the codebase, the tools used and *why* those over alternatives, annotated code excerpts, and a "how to explain this in 30 seconds" framing.
- **What shipped (25%)** — git activity from the last 24 hours, translated to technical-but-readable language.
- **What's next (15%)** — short orientation toward the next concrete piece of work.

Spotlight rotation is weighted: files changed in the last 24h are preferred, weighted against a coverage log (`.brief/coverage.json`) so the same area doesn't get rehashed. Falls back to a curated curriculum list when no fresh changes warrant a spotlight.

Cost: roughly one Claude API call per run. Sonnet 4.6, ~$0.10/day.

---

## Install

### Option A — `pipx` (recommended once set up)

```bash
# install pipx if you don't have it
brew install pipx
pipx ensurepath

# install daily-brief globally
pipx install git+https://github.com/halesoyster/daily_brief.git
```

After this, `daily-brief` is on your `$PATH`.

### Option B — clone + venv (no extra tooling)

```bash
git clone https://github.com/halesoyster/daily_brief.git
cd daily_brief
python3 -m venv .venv
.venv/bin/pip install -e .
# invoke as: .venv/bin/daily-brief OR .venv/bin/python daily_brief.py
```

---

## Configure

### Environment variables

| Var | Required for | Purpose |
|---|---|---|
| `CLAUDE_API_KEY` *(or* `ANTHROPIC_API_KEY`*)* | always | Claude API auth |
| `BRIEF_EMAIL_FROM` | `--email` | Gmail address sending the brief |
| `BRIEF_EMAIL_TO` | `--email` | Inbox the brief lands in (can equal FROM) |
| `BRIEF_EMAIL_APP_PASSWORD` | `--email` | Gmail **App Password** (16 chars). NOT your regular password. Generate at https://myaccount.google.com/apppasswords (requires 2FA). |

If any of the three email vars is missing, `--email` is a graceful no-op (logs a notice, doesn't fail the run).

### Per-project config

Today (v0.1): when daily_brief is pointed at a project, it **auto-detects** project type by reading the project's `CLAUDE.md` and adjusts:

- **moon_baby project** (CLAUDE.md mentions `moon_baby`) — uses the moon_baby-specific curriculum, sprint state file paths, and audience framing.
- **Anything else** — generic developer-reading-their-own-codebase mode.

A real config file (YAML) is planned for v0.2. Until then, generalizing beyond moon_baby means editing the `MOON_BABY_DEFAULTS` and `GENERIC_DEFAULTS` dicts in `daily_brief.py`.

---

## Run

```bash
# brief against the current directory's project
daily-brief

# brief against another project
daily-brief --project ~/Projects/moon_baby

# brief + send via email (needs BRIEF_EMAIL_* env vars)
daily-brief --project ~/Projects/moon_baby --email

# don't open in browser (useful for headless / cron / launchd)
daily-brief --project ~/Projects/moon_baby --email --no-open

# print to stdout instead of saving
daily-brief --dry-run
```

Brief HTML is saved to `<project>/.brief/<YYYY-MM-DD>.html`. Coverage log at `<project>/.brief/coverage.json`.

---

## Schedule it (macOS LaunchAgent)

```bash
# install — fires at 9am against the specified project
bash install.sh /absolute/path/to/your/project

# test immediately
launchctl kickstart -p gui/$(id -u)/com.daily-brief

# check logs
tail -f ~/Library/Logs/daily-brief/run.log
```

`install.sh` rewrites path placeholders in `com.daily-brief.plist` and bootstraps via the modern `launchctl bootstrap` API (the legacy `launchctl load` silently no-ops on recent macOS).

**Important on macOS:** launchd-spawned processes cannot read `~/Documents/`, `~/Downloads/`, or `~/Desktop/` due to TCC restrictions. Keep your project in `~/Projects/`, `~/dev/`, or any non-protected location.

---

## Status

- **v0.1:** Extraction from `moon_baby/scripts/morning_brief.py`. Auto-detects moon_baby vs. generic projects. Email delivery via Gmail SMTP.
- **v0.2 (next):** YAML per-project config file (`.daily-brief.yaml`) replacing hardcoded defaults. Allows any project to configure curriculum, sprint state files, and audience framing without editing `daily_brief.py`.
- **v0.3 (done):** Standalone `run.sh` + `install.sh` + plist template. LaunchAgent cut over from moon_baby's in-tree script. morning_brief.py removed from moon_baby.

---

## License

MIT
