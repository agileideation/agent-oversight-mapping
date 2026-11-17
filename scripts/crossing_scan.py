"""Crossing scan: the machine half of Agent Oversight Mapping.

A crossing is one place where an agent, acting on its own, changes something
outside the area it works in (a merge, a deploy, a message sent, a record
written, money spent). This script lists candidate crossings from what is
written down in repositories and on this machine, and compares the list with
a saved baseline so a person only has to read what changed.

It is deterministic and stdlib-only. It reads settings AND history. It never
opens secret files and never prints a secret value. It cannot see live cloud
settings, provider dashboards, or keys: those come from the interview.

Usage: see README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import fnmatch
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = 2

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", "vendor", "__pycache__",
    ".venv", "venv", "coverage", "_archive", ".pytest_cache", "worktrees",
}
# Never opened, whatever the detector. WHOLE file or folder names only, matched
# on the path relative to the scan root, so `src/secretary/` and
# `lib/secretCore.ts` are read and `.env.local` is not.
SECRET_GLOBS = (
    ".env", ".env.*", ".local-secrets", "secrets", "secrets.*", "credentials",
    "credentials.*", "*.pem", "*.key", "*.pfx", "id_rsa*", "id_ed25519*",
    ".netrc", ".npmrc", ".pypirc",
)
# Anything token-shaped is replaced before it reaches a row.
_URL_CRED = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_BEARER = re.compile(r"(?i)\b(bearer)\s+\S+")
_NAMED = re.compile(
    r"(?i)\b(token|api[_-]?key|key|secret|password|passwd|pwd|authorization)\b(\s*[=:]\s*|\s+)(\S+)"
)
# Long runs that look like a token and not like a folder name: a known prefix,
# long hex, mixed case with digits, or 24+ lowercase letters and digits with no
# separators. Hyphenated slugs such as `2025-01-15-some-project-name` are kept.
_LONG = re.compile(
    r"\b(?:glpat-|ghp_|gho_|github_pat_|sk-|xox[abpr]-|AKIA|eyJ)[A-Za-z0-9_.\-]{8,}"
    r"|\b[0-9a-f]{32,}\b"
    r"|\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[a-z])(?=[A-Za-z0-9_\-]*[A-Z])[A-Za-z0-9_\-]{24,}\b"
    r"|\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{24,}\b"
)
TEXT_EXT = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".sh", ".ps1", ".rb", ".go",
    ".yml", ".yaml", ".json", ".toml", ".md",
}
MAX_BYTES = 1_000_000

AGENT_TRAILER = re.compile(
    r"(co-authored-by:.*(claude|codex|copilot|gemini|cursor|gpt)|noreply@anthropic\.com"
    r"|generated with \[?claude code)",
    re.IGNORECASE,
)
CI_ACTS = re.compile(
    r"(deploy|provision|release|publish|bootstrap|migrat|webhook|upload|notify|rollout)",
    re.IGNORECASE,
)
TASK_ACTS = re.compile(
    r"\b(push|commit|deploy|send|post|publish|delete|merge|schedule|email)\w*", re.IGNORECASE
)
OUTBOUND = {
    "message": [
        "api.telegram.org", "hooks.slack.com", "discord.com/api/webhooks",
        "api.sendgrid.com", "api.twilio.com", "smtplib", "nodemailer",
        "api.linkedin.com",
    ],
    "spend": [
        "api.openai.com", "api.anthropic.com", "openrouter.ai/api",
        "api.deepinfra.com", "generativelanguage.googleapis.com",
    ],
}
LIMITS = [
    "Keys and passwords are not scanned. Ask about them in the interview.",
    "Live settings in cloud accounts, provider dashboards and hosting panels are not visible here.",
    "Scheduled work is found only where this scan was pointed.",
    "A scan that finds nothing new only covers what a scan can see.",
]


# ---------------------------------------------------------------- helpers

def _run(args, cwd, timeout=60):
    """Run a command. Returns (ok, stdout). Never raises."""
    try:
        p = subprocess.run(
            args, cwd=str(cwd), capture_output=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, ""
    return p.returncode == 0, p.stdout.decode("utf-8", errors="replace")


def _rid(repo, kind, locator):
    return hashlib.sha1(f"{repo}|{kind}|{locator}".encode("utf-8")).hexdigest()[:12]


def _redact(text):
    """Replace anything token-shaped. Rows carry names and places, never values."""
    text = _URL_CRED.sub("://[redacted]@", str(text))
    text = _BEARER.sub(lambda m: m.group(1) + " [redacted]", text)
    text = _NAMED.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
    return _LONG.sub("[redacted]", text)


def _row(repo, kind, locator, title, trigger, guardrail, flag, evidence, reach, actor=""):
    reach = {k: ([_redact(x) for x in v] if isinstance(v, list) else v) for k, v in reach.items()}
    return {
        "id": _rid(repo, kind, locator), "repo": repo, "kind": kind,
        "title": title, "trigger": trigger, "guardrail": guardrail, "flag": flag,
        "evidence": [_redact(e) for e in evidence[:6]], "reach": reach, "history_actor": actor,
    }


def _is_secret(rel):
    """True when any whole name in a path RELATIVE to the scan root is a secret name."""
    return any(
        fnmatch.fnmatchcase(part.lower(), glob)
        for part in Path(rel).parts for glob in SECRET_GLOBS
    )


def _band(count):
    """Volume as a coarse band, so a jump in volume shows up as 'now does more'."""
    return 0 if count <= 0 else 1 if count < 10 else 2 if count < 100 else 3


def _files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not _is_secret(d)]
        for name in filenames:
            p = Path(dirpath) / name
            if _is_secret(p.relative_to(root)) or p.suffix.lower() not in TEXT_EXT:
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield p


def _read(path, root=None):
    """Read a text file unless its path, relative to the scan root, names a secret."""
    path = Path(path)
    try:
        rel = path.relative_to(root) if root is not None else Path(path.name)
    except ValueError:
        rel = Path(path.name)
    if _is_secret(rel):
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# -------------------------------------------------------------- detectors

def scan_history(name, root, branch, days, fetch, fetch_timeout):
    """Read what actually landed on the default branch, and under whose name."""
    rows, notes = [], []
    if not (Path(root) / ".git").exists():
        return rows, [f"{name}: not a git checkout, history skipped"]
    fetched = False
    if fetch:
        fetched, _ = _run(["git", "fetch", "--quiet", "origin"], root, fetch_timeout)
        if not fetched:
            notes.append(f"{name}: could not fetch; this copy may be old")
    if not branch:
        ok, out = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
        branch = out.strip().split("/", 1)[-1] if ok and out.strip() else "master"
    ref = f"origin/{branch}"
    ok, out = _run(["git", "log", "-1", "--format=%H|%cI", ref], root)
    if not ok:
        return rows, notes + [f"{name}: no {ref}, history skipped"]
    head, head_date = (out.strip().split("|") + [""])[:2]
    fmt = "%H%x1f%P%x1f%cn%x1f%B%x1e"
    ok, out = _run(
        ["git", "log", "--first-parent", f"--since={days} days ago", f"--format={fmt}", ref], root
    )
    # One pass over recent commits on every branch that reached the default
    # branch, so a merge can be checked by the tip of the branch it brought in.
    _, every = _run(
        ["git", "log", f"--since={days + 60} days ago", "--format=%H%x1f%B%x1e", ref], root, 120
    )
    marked_shas = set()
    for rec in every.split("\x1e"):
        sha, _, body = rec.strip("\n").partition("\x1f")
        if sha and AGENT_TRAILER.search(body):
            marked_shas.add(sha)
    merges = direct = agent_merges = agent_direct = 0
    actors = {}
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        sha, parents, committer, body = parts[0], parts[1].split(), parts[2], parts[3]
        marked = bool(AGENT_TRAILER.search(body))
        if len(parents) > 1:
            merges += 1
            marked = marked or parents[1] in marked_shas  # tip of the merged branch
            agent_merges += marked
        else:
            direct += 1
            agent_direct += marked
        actors[committer] = actors.get(committer, 0) + 1
    actor = ", ".join(f"{k} ({v})" for k, v in sorted(actors.items(), key=lambda kv: -kv[1])[:3])
    stamp = f"{ref} at {head[:9]} ({head_date[:10]}), fetched={'yes' if fetched else 'no'}"
    if merges:
        rows.append(_row(
            name, "history-merge", branch,
            f"Changes are merged into {branch}",
            f"{merges} merges in {days} days; at least {agent_merges} carry an agent's mark",
            "not visible in history; ask who looked before each merge",
            "LIVE", [stamp],
            {"any": True, "agent_marked": agent_merges > 0, "volume_band": _band(merges)}, actor,
        ))
    if direct:
        rows.append(_row(
            name, "history-direct", branch,
            f"Changes land on {branch} with no merge request",
            f"{direct} direct commits in {days} days; at least {agent_direct} carry an agent's mark",
            "none visible",
            "LIVE", [stamp],
            {"any": True, "agent_marked": agent_direct > 0, "volume_band": _band(direct)}, actor,
        ))
    if fetch and not fetched:
        rows.append(_row(
            name, "stale-copy", "fetch", "This copy could not be refreshed before the scan",
            "git fetch failed or timed out", "n/a", "UNKNOWN-LIVE", [stamp], {"any": True},
        ))
    return rows, notes


def _without_blocks(text, names=("artifacts", "cache", "retry")):
    """Drop sub-blocks whose own 'when:' is not about whether the job runs."""
    out, skip_indent = [], None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if skip_indent is not None:
            if line.strip() and indent <= skip_indent:
                skip_indent = None
            else:
                continue
        if re.match(r"\s*(%s):" % "|".join(names), line):
            skip_indent = indent
            continue
        out.append(line)
    return "\n".join(out)


def scan_ci(name, root):
    """Jobs in .gitlab-ci.yml that change something outside the pipeline."""
    rows = []
    path = Path(root) / ".gitlab-ci.yml"
    text = _read(path, root)
    if not text:
        return rows
    job, block, start = None, [], 0
    jobs = []
    for i, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.\- ]*):\s*(#.*)?$", line)
        if m and not line.startswith((" ", "\t")):
            if job:
                jobs.append((job, start, "\n".join(block)))
            job, block, start = m.group(1).strip(), [], i
        elif job:
            block.append(line)
    if job:
        jobs.append((job, start, "\n".join(block)))
    reserved = {"stages", "variables", "default", "include", "workflow", "image", "cache"}
    for job, start, body in jobs:
        if job in reserved or not ("script:" in body):
            continue
        code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
        if not CI_ACTS.search(job) and not CI_ACTS.search(code):
            continue
        rules_text = _without_blocks(code)
        when_list = re.findall(r"when:\s*([a-z_]+)", rules_text)
        whens = set(when_list)
        rule_items = len(re.findall(r"^\s*-\s*(?:if|changes|exists|when):", rules_text, re.MULTILINE))
        implicit = rule_items > len(when_list)  # a rule with no 'when' runs on success
        auto = bool(whens & {"always", "on_success", "delayed"}) or not whens or implicit
        manual = "manual" in whens and not auto
        off = whens == {"never"} and not implicit
        rows.append(_row(
            name, "ci-job", job, f"CI job '{job}' acts outside the pipeline",
            "a person starts it" if manual else ("switched off" if off else "runs when the pipeline runs"),
            "a person has to start it" if manual else ("rule: never" if off else "none in the config"),
            "LATENT" if (manual or off) else "LIVE",
            [f".gitlab-ci.yml:{start}"], {"auto": auto and not off},
        ))
    return rows


GH_EVENTS_MANUAL = {"workflow_dispatch"}
GH_ACTS = re.compile(
    r"(pages|docker/build-push|docker push|gh release|gh pr merge|git push|npm publish|twine upload"
    r"|aws-actions|azure/|google-github-actions|terraform apply|kubectl apply)",
    re.IGNORECASE,
)


def _gh_events(text):
    """Event names and timers from a workflow's top-level 'on:' key."""
    events, crons, inside = [], [], False
    for line in text.splitlines():
        m = re.match(r"""^['"]?on['"]?:\s*(.*)$""", line)
        if m:
            rest = m.group(1).split("#")[0].strip()
            if rest:
                events += re.findall(r"[a-z_]+", rest)
                break
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith((" ", "\t")):
                break
            item = re.match(r"^\s+-\s*([a-z_]+)\s*$", line)
            if item and not crons:
                events.append(item.group(1))
                continue
            m = re.match(r"^(\s+)([a-z_]+):", line)
            if m and (not events or len(m.group(1)) == first):
                first = len(m.group(1))
                events.append(m.group(2))
            c = re.search(r"cron:\s*['\"]?([^'\"#]+)", line)
            if c:
                crons.append(c.group(1).strip())
    return sorted(set(events)), crons


def scan_github_actions(name, root):
    """Jobs in .github/workflows/ that change something outside the workflow run."""
    rows = []
    folder = Path(root) / ".github" / "workflows"
    if not folder.is_dir():
        return rows
    for path in sorted(folder.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        rel = path.relative_to(root).as_posix()
        raw = _read(path, root)
        text = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
        if not text.strip():
            continue
        events, crons = _gh_events(text)
        manual_only = bool(events) and set(events) <= GH_EVENTS_MANUAL
        called_only = events == ["workflow_call"]
        top_block = re.search(r"(?m)^permissions:\s*\n((?:[ \t]+.*\n?)+)", text)
        top_list = sorted(set(re.findall(r"(?m)^\s+([a-z\-]+):\s*write\s*$", top_block.group(1)))) if top_block else []
        top_writes = bool(top_list) or bool(re.search(r"(?m)^permissions:\s*write-all", text))
        jobs, job, block, start, indent, in_jobs = [], None, [], 0, None, False
        for i, line in enumerate(raw.splitlines(), 1):
            if re.match(r"^jobs:\s*(#.*)?$", line):
                in_jobs = True
                continue
            if not in_jobs:
                continue
            if line.strip() and not line.startswith((" ", "\t")):
                break
            m = re.match(r"^(\s+)([A-Za-z0-9_][A-Za-z0-9_\-]*):\s*(#.*)?$", line)
            if m and (indent is None or len(m.group(1)) == indent):
                indent = len(m.group(1))
                if job:
                    jobs.append((job, start, "\n".join(block)))
                job, block, start = m.group(2), [], i
            elif job:
                block.append(line)
        if job:
            jobs.append((job, start, "\n".join(block)))
        for job, start, body in jobs:
            code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
            writes = sorted(set(re.findall(r"(?m)^\s+([a-z\-]+):\s*write\s*$", code)))
            env = re.search(r"(?m)^\s+environment:\s*(?:\n\s+name:\s*)?['\"]?([A-Za-z0-9_.\-]+)", code)
            if not (CI_ACTS.search(job) or CI_ACTS.search(code) or GH_ACTS.search(code) or writes or top_writes or env):
                continue
            if manual_only:
                trigger, flag = "a person starts it", "LATENT"
            elif called_only:
                trigger, flag = "another workflow calls it", "UNKNOWN-LIVE"
            elif events:
                trigger, flag = "runs on: " + ", ".join(events), "LIVE"
            else:
                trigger, flag = "trigger not readable", "UNKNOWN-LIVE"
            if manual_only:
                guard = "a person has to start it"
            elif env:
                guard = f"environment '{env.group(1)}' (approval rules, if any, are not visible in the repo)"
            else:
                guard = "none in the config"
            reach = {"auto": not manual_only and not called_only and bool(events), "events": events}
            if crons:
                reach["cron"] = "; ".join(crons)
            if writes or top_writes:
                reach["writes"] = sorted(set(writes) | set(top_list)) or ["all"]
            rows.append(_row(
                name, "ci-job", f"{rel}:{job}", f"CI job '{job}' acts outside the pipeline",
                trigger, guard, flag, [f"{rel}:{start}"], reach,
            ))
    return rows


def scan_deploy(name, root, app_dirs=None):
    """Deploy settings that take effect on their own.

    `app_dirs` lists folders, relative to the repo, that hold one JSON app
    definition per hosted service.
    """
    rows = []
    root = Path(root)
    text = _read(root / "render.yaml", root)
    if text:
        auto = bool(re.search(r"autoDeploy:\s*true", text))
        pre = re.search(r"preDeployCommand:\s*(.+)", text)
        rows.append(_row(
            name, "deploy-config", "render.yaml",
            "Pushes to the deploy branch go live" if auto else "Deploy config present",
            "every push to the deploy branch" if auto else "not automatic in the config",
            "none in the config" if auto else "automatic deploy is off in the config",
            "LIVE" if auto else "LATENT",
            ["render.yaml"] + ([f"runs before start: {pre.group(1).strip()}"] if pre else []),
            {"auto": auto, "pre_deploy": bool(pre)},
        ))
    for fname in ("vercel.json", "netlify.toml", "fly.toml"):
        if (root / fname).exists():
            rows.append(_row(
                name, "deploy-config", fname, f"Hosting config present ({fname})",
                "set in the hosting account", "not visible here", "UNKNOWN-LIVE", [fname],
                {"auto": False},
            ))
    for rel_dir in app_dirs or []:
        rel_dir = str(rel_dir).strip("/")
        folder = root / rel_dir
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            if _is_secret(f"{rel_dir}/{f.name}"):
                continue
            rows.append(_row(
                name, "deploy-config", f"{rel_dir}/{f.name}",
                f"App definition for a hosted service ({f.stem})",
                "set in the hosting panel", "not visible here", "UNKNOWN-LIVE",
                [f"{rel_dir}/{f.name}"], {"auto": False},
            ))
    return rows


def scan_code(name, root, extra=None):
    """Schedules written in code, and places code sends messages or spends.

    `extra` adds your own hosts to look for: {"message": [...], "spend": [...]}.
    """
    rows = []
    root = Path(root)
    hits = {}
    outbound = {
        kind: needles + [str(n).lower() for n in (extra or {}).get(kind, []) if str(n).strip()]
        for kind, needles in OUTBOUND.items()
    }
    for p in _files(root):
        rel = p.relative_to(root).as_posix()
        if "/tests/" in f"/{rel}" or rel.startswith("docs/") or p.suffix == ".md":
            continue
        if p.name == Path(__file__).name:
            continue  # this script lists the hosts it looks for, so it would find itself
        lines = _read(p, root).splitlines()
        for i, line in enumerate(lines, 1):
            if ".schedule(" in line:
                window = " ".join(lines[i - 1:i + 4])
                arg = r"['\"]?([A-Za-z0-9_.*/ ,\-]+?)['\"]?"  # a literal or a named constant
                m = re.search(r"\.schedule\(\s*" + arg + r"\s*,\s*" + arg + r"\s*[,)]", window)
                if m:
                    rows.append(_row(
                        name, "schedule", f"{rel}:{m.group(1)}",
                        f"Scheduled job '{m.group(1)}' inside the application",
                        f"on a timer ({m.group(2)})", "none in the code", "UNKNOWN-LIVE",
                        [f"{rel}:{i}"], {"cron": m.group(2)},
                    ))
            low = line.lower()
            for kind, needles in outbound.items():
                for needle in needles:
                    if needle in low:
                        hits.setdefault((kind, needle), []).append(f"{rel}:{i}")
    for (kind, needle), where in sorted(hits.items()):
        files = sorted({w.rsplit(":", 1)[0] for w in where})
        rows.append(_row(
            name, f"outbound-{kind}", needle,
            ("Code can send messages through " if kind == "message" else "Code can spend through ") + needle,
            "whenever that code runs", "not visible in the code", "UNKNOWN-LIVE",
            where, {"files": len(files)},
        ))
    return rows


def scan_tools(name, root):
    """Tools and connections agents have been given in this repo."""
    rows = []
    for rel in (".mcp.json", ".claude/settings.json"):
        text = _read(Path(root) / rel, root)
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        for server in sorted((data.get("mcpServers") or {}).keys()):
            rows.append(_row(
                name, "agent-tool", f"{rel}:{server}", f"Agents here are connected to '{server}'",
                "whenever an agent uses it", "whatever the agent's permissions allow",
                "UNKNOWN-LIVE", [rel], {"any": True},
            ))
        allow = ((data.get("permissions") or {}).get("allow")) or []
        acting = sorted(a for a in allow if isinstance(a, str) and TASK_ACTS.search(a))
        if acting:
            rows.append(_row(
                name, "agent-permission", rel,
                "Agents here are pre-approved for commands that act outside",
                "whenever an agent runs one", "pre-approved, so nobody is asked",
                "LIVE", [rel] + acting[:5], {"acts": acting},
            ))
    return rows


def scan_agent_tasks(dirs):
    """Scheduled agent tasks that live on this machine, outside any repo."""
    rows = []
    for d in dirs:
        base = Path(os.path.expanduser(d))
        if not base.is_dir():
            continue
        for task in sorted(p for p in base.iterdir() if p.is_dir()):
            text = "".join(_read(f, base) for f in sorted(task.glob("*.md")))
            acts = sorted({m.group(1).lower() for m in TASK_ACTS.finditer(text)})
            rows.append(_row(
                "this-machine", "agent-task", f"{base.name}/{task.name}",
                f"Scheduled agent task '{task.name}' on this machine",
                "on a timer set in the agent tool", "not visible here",
                "UNKNOWN-LIVE", [str(task)], {"acts": acts},
            ))
    return rows


# ------------------------------------------------------------ scan + diff

def scan(config, fetch=True):
    rows, notes = [], []
    days = int(config.get("lookback_days", 30))
    timeout = int(config.get("fetch_timeout_seconds", 60))
    for repo in config.get("repos", []):
        name, root = repo["name"], os.path.expanduser(repo["path"])
        if not Path(root).is_dir():
            notes.append(f"{name}: path not found, skipped")
            continue
        got, more = scan_history(name, root, repo.get("default_branch"), days, fetch, timeout)
        rows += got
        notes += more
        rows += scan_ci(name, root) + scan_github_actions(name, root)
        rows += scan_deploy(name, root, config.get("app_definition_dirs"))
        rows += scan_code(name, root, config.get("extra_outbound")) + scan_tools(name, root)
    rows += scan_agent_tasks(config.get("agent_task_dirs", []))
    seen, unique = set(), []
    for r in rows:  # same locator found twice keeps the first, merges evidence
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        unique.append(r)
    return unique, notes


def _widened(old, new):
    a, b = old.get("reach") or {}, new.get("reach") or {}
    why = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        if isinstance(y, bool) and y and not x:
            why.append(f"{key}: now yes")
        elif isinstance(y, int) and not isinstance(y, bool) and isinstance(x, int) and y > x:
            why.append(f"{key}: {x} -> {y}")
        elif isinstance(y, list) and set(y) - set(x or []):
            why.append(f"{key}: added {', '.join(sorted(set(y) - set(x or [])))}")
        elif key == "cron" and x and y and x != y:
            why.append(f"timer changed: {x} -> {y}")
    return why


def _claim_is_stale(human, today, max_age_days):
    """A named person on a row is a claim. It needs a 'confirmed' date that is recent."""
    involved = str(human.get("involved") or "").strip().lower()
    if not involved or involved == "nobody":
        return None
    confirmed = str(human.get("confirmed") or "")
    try:
        age = (today - date.fromisoformat(confirmed[:10])).days
    except ValueError:
        return "no confirmed date"
    return f"last confirmed {age} days ago" if age > max_age_days else None


def diff(baseline_rows, rows, today=None, max_age_days=90):
    """Three signals a person reads, plus rows not found again and claims gone stale."""
    today = today or date.today()
    out = {"new": [], "widened": [], "drifted": [], "not_found": [], "stale_claims": []}
    now = {r["id"]: r for r in rows}
    for rid, r in now.items():
        old = baseline_rows.get(rid)
        if old is None:
            out["new"].append(r)
            continue
        if old.get("retired"):
            out["new"].append({**r, "why": ["marked retired in the baseline, and found again"]})
            continue
        why = _widened(old, r)
        if why:
            out["widened"].append({**r, "why": why})
        human = old.get("human") or {}
        involved = str(human.get("involved") or "").strip().lower()
        nobody_in_config = r["flag"] == "LIVE" and r["guardrail"].startswith(("none", "pre-approved"))
        if involved and involved != "nobody" and nobody_in_config:
            out["drifted"].append({
                **r, "why": [f"the map says {human.get('involved')} is involved; "
                             f"the scan finds no guardrail ({r['guardrail']})"],
            })
    for rid, old in baseline_rows.items():
        if old.get("retired"):
            continue
        if rid not in now:
            out["not_found"].append(old)
        stale = _claim_is_stale(old.get("human") or {}, today, max_age_days)
        if stale:
            out["stale_claims"].append({**old, "why": [
                f"{(old.get('human') or {}).get('involved')} is named as involved; {stale}"]})
    return out


def merge_baseline(baseline_rows, rows, when=None):
    """New baseline. Nothing is ever dropped.

    Rows found this time get a fresh machine half and today's 'last_seen'.
    Rows not found keep everything they had, the person's half included, so a
    missing repo path or a renamed job cannot erase interview answers. Remove
    a row by hand, after it has been marked retired.
    """
    when = when or date.today().isoformat()
    merged = {rid: dict(old) for rid, old in baseline_rows.items()}
    for r in rows:
        old = baseline_rows.get(r["id"], {})
        merged[r["id"]] = {
            **r, "human": old.get("human", {}), "retired": bool(old.get("retired")),
            "last_seen": when,
        }
    return merged


def report_md(rows, notes, changes):
    lines = ["# Crossing scan", ""]
    by_flag = {}
    for r in rows:
        by_flag[r["flag"]] = by_flag.get(r["flag"], 0) + 1
    lines.append(f"{len(rows)} rows: " + ", ".join(f"{k} {v}" for k, v in sorted(by_flag.items())) + ".")
    lines.append("These are counts of what the scan found, not a grade.")
    if changes is not None:
        for key, label in (
            ("new", "New"), ("widened", "Now does more"),
            ("drifted", "The map says a person is involved and the scan finds no guardrail"),
            ("not_found", "Not found this time (confirm it was shut down, then mark it retired)"),
            ("stale_claims", "A person is named, and nobody has confirmed it lately"),
        ):
            lines += ["", f"## {label} ({len(changes[key])})"]
            for r in changes[key]:
                why = "; ".join(r.get("why", []))
                lines.append(f"- [{r['repo']}] {r['title']}" + (f" ({why})" if why else "")
                             + f" | {r['evidence'][0] if r.get('evidence') else ''}")
    else:
        lines += ["", "## All rows (no baseline given)"]
        for r in rows:
            lines.append(f"- [{r['repo']}] {r['flag']}: {r['title']} | {r['trigger']}"
                         + (f"; history shows {r['history_actor']}" if r["history_actor"] else ""))
    if notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in notes]
    lines += ["", "## What this scan cannot see"] + [f"- {l}" for l in LIMITS]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- selftest

def selftest():
    """Plant a crossing in a throwaway repo and check the scan finds it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "planted"
        (root / "scripts" / "weekly-recap-mailer").mkdir(parents=True)
        (root / ".gitlab-ci.yml").write_text(
            "stages:\n  - deploy\n\nship-it:\n  stage: deploy\n  script:\n    - ./deploy.sh\n",
            encoding="utf-8")
        cfg = {"repos": [{"name": "planted", "path": str(root)}]}
        before, _ = scan(cfg, fetch=False)
        base = merge_baseline({}, before)
        (root / "scripts" / "weekly-recap-mailer" / "send.py").write_text(
            "import smtplib\n# emails a weekly recap to everyone\n", encoding="utf-8")
        after, _ = scan(cfg, fetch=False)
        changes = diff(base, after)
        found = [r for r in changes["new"] if r["kind"] == "outbound-message"]
        ok = bool(found) and any(r["kind"] == "ci-job" for r in before)
        print("selftest:", "PASS" if ok else "FAIL",
              f"(planted crossing {'found' if found else 'MISSED'} as new)")
        return 0 if ok else 1


# -------------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="JSON file listing the repos and task folders to scan")
    ap.add_argument("--baseline", help="saved baseline JSON to compare against")
    ap.add_argument("--write-baseline", action="store_true",
                    help="save this scan as the new baseline (keeps the person's half)")
    ap.add_argument("--no-fetch", action="store_true", help="do not refresh the repos first")
    ap.add_argument("--out", help="write the full result as JSON")
    ap.add_argument("--md", help="write the short report as Markdown")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.selftest:
        return selftest()
    if not args.config:
        ap.error("--config is required")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    rows, notes = scan(config, fetch=not args.no_fetch)
    baseline_rows, changes = {}, None
    if args.baseline and Path(args.baseline).exists():
        baseline_rows = json.loads(Path(args.baseline).read_text(encoding="utf-8")).get("rows", {})
        changes = diff(baseline_rows, rows, max_age_days=int(config.get("claim_max_age_days", 90)))
    result = {
        "schema": SCHEMA, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": rows, "notes": notes, "changes": changes, "limits": LIMITS,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = report_md(rows, notes, changes)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    if args.write_baseline:
        if not args.baseline:
            ap.error("--write-baseline needs --baseline <file>")
        Path(args.baseline).write_text(json.dumps({
            "schema": SCHEMA, "generated": result["generated"],
            "rows": merge_baseline(baseline_rows, rows),
        }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
