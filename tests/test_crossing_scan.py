"""Tests for the crossing scan. Run: python -m pytest tests -q

The history test builds a real git repo, so the runner needs a git binary and
a git identity (user.name and user.email).
"""
import importlib.util
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "crossing_scan", Path(__file__).resolve().parents[1] / "scripts" / "crossing_scan.py"
)
cs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cs)

THREE_MANUAL_JOBS = (
    "deploy-a:\n  script:\n    - ./deploy.sh\n  rules:\n    - when: manual\n\n"
    "deploy-b:\n  script:\n    - ./deploy.sh\n  rules:\n    - when: manual\n\n"
    "deploy-c:\n  script:\n    - ./deploy.sh\n  rules:\n    - when: manual\n"
)


def _cfg(root, **extra):
    return {"repos": [{"name": "r", "path": str(root)}], **extra}


def _title(job):
    return f"CI job '{job}' acts outside the pipeline"


def test_selftest_finds_the_planted_crossing():
    assert cs.selftest() == 0


def test_ci_job_manual_auto_and_off(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text(
        "stages:\n  - x\n\n"
        "unit-tests:\n  script:\n    - pytest\n\n"
        "deploy-auto:\n  script:\n    - ./deploy.sh\n  artifacts:\n    when: always\n\n"
        "deploy-manual:\n  script:\n    - ./deploy.sh\n  rules:\n    - if: $X\n      when: never\n    - when: manual\n\n"
        "deploy-off:\n  script:\n    - ./deploy.sh\n  rules:\n    - when: never\n\n"
        "deploy-implicit:\n  script:\n    - ./deploy.sh\n  rules:\n    - if: $X\n      when: never\n    - if: $Y\n",
        encoding="utf-8",
    )
    rows = {r["title"].split("'")[1]: r for r in cs.scan_ci("r", tmp_path)}
    assert "unit-tests" not in rows
    assert rows["deploy-auto"]["flag"] == "LIVE" and rows["deploy-auto"]["reach"]["auto"]
    assert rows["deploy-manual"]["flag"] == "LATENT" and not rows["deploy-manual"]["reach"]["auto"]
    assert rows["deploy-off"]["flag"] == "LATENT" and not rows["deploy-off"]["reach"]["auto"]
    assert rows["deploy-implicit"]["flag"] == "LIVE"


def test_the_scan_does_not_find_its_own_host_list(tmp_path):
    here = Path(__file__).resolve().parents[1] / "scripts" / "crossing_scan.py"
    (tmp_path / "scripts").mkdir()
    shutil.copy(here, tmp_path / "scripts" / "crossing_scan.py")
    (tmp_path / "ok.py").write_text("HOST = 'api.anthropic.com'\n", encoding="utf-8")
    rows = cs.scan_code("r", tmp_path)
    assert [r["title"] for r in rows] == ["Code can spend through api.anthropic.com"]


def test_extra_outbound_hosts_come_from_the_config(tmp_path):
    (tmp_path / "send.py").write_text("URL = 'https://Alerts.Example.com/api/v0/alerts'\n", encoding="utf-8")
    assert cs.scan_code("r", tmp_path) == []
    rows = cs.scan_code("r", tmp_path, {"message": ["alerts.example.com/api"]})
    assert [r["kind"] for r in rows] == ["outbound-message"]
    config = {"repos": [{"name": "r", "path": str(tmp_path)}],
              "extra_outbound": {"message": ["alerts.example.com/api"]}}
    got, _ = cs.scan(config, fetch=False)
    assert "outbound-message" in [r["kind"] for r in got]


def _workflow(tmp_path, fname, text):
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / fname).write_text(text, encoding="utf-8")


def test_github_workflow_auto_manual_and_plain_tests(tmp_path):
    _workflow(tmp_path, "ci.yml",
              "name: ci\non:\n  push:\n    branches: [main]\n  schedule:\n    - cron: '0 6 * * 1'\n"
              "jobs:\n  unit:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
              "  ship:\n    runs-on: ubuntu-latest\n    environment: production\n    steps:\n      - run: ./deploy.sh\n")
    _workflow(tmp_path, "manual.yaml",
              "on: workflow_dispatch\njobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm publish\n")
    rows = {r["title"].split("'")[1]: r for r in cs.scan_github_actions("r", tmp_path)}
    assert set(rows) == {"ship", "release"}
    ship = rows["ship"]
    assert ship["flag"] == "LIVE" and ship["reach"]["auto"]
    assert ship["reach"]["events"] == ["push", "schedule"] and ship["reach"]["cron"] == "0 6 * * 1"
    assert "production" in ship["guardrail"]
    assert rows["release"]["flag"] == "LATENT" and not rows["release"]["reach"]["auto"]


def test_github_job_with_write_permission_is_listed(tmp_path):
    _workflow(tmp_path, "bot.yml",
              "on: [pull_request, push]\njobs:\n  tidy:\n    runs-on: ubuntu-latest\n"
              "    permissions:\n      contents: write\n    steps:\n      - run: ./format.sh\n")
    rows = cs.scan_github_actions("r", tmp_path)
    assert len(rows) == 1 and rows[0]["reach"]["writes"] == ["contents"]
    assert rows[0]["reach"]["events"] == ["pull_request", "push"]
    _workflow(tmp_path, "top.yml",
              "on:\n  push:\n    tags:\n      - 'v*'\n\npermissions:\n  contents: write\n\n"
              "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make\n")
    top = [r for r in cs.scan_github_actions("r", tmp_path) if "'build'" in r["title"]]
    assert len(top) == 1 and top[0]["reach"]["writes"] == ["contents"] and top[0]["reach"]["events"] == ["push"]
    got, _ = cs.scan({"repos": [{"name": "r", "path": str(tmp_path)}]}, fetch=False)
    assert any(r["kind"] == "ci-job" for r in got)


def test_app_definition_folders_come_from_the_config(tmp_path):
    (tmp_path / "ops" / "apps").mkdir(parents=True)
    (tmp_path / "ops" / "apps" / "site.json").write_text("{}", encoding="utf-8")
    assert cs.scan_deploy("r", tmp_path) == []
    rows = cs.scan_deploy("r", tmp_path, ["ops/apps"])
    assert [r["title"] for r in rows] == ["App definition for a hosted service (site)"]
    assert rows[0]["evidence"] == ["ops/apps/site.json"]


def test_secret_files_are_never_read(tmp_path):
    (tmp_path / ".env").write_text("URL=https://api.telegram.org/botTOKEN\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("URL=https://api.twilio.com/TOKEN\n", encoding="utf-8")
    (tmp_path / ".local-secrets").mkdir()
    (tmp_path / ".local-secrets" / "keys.py").write_text("x='api.openai.com'\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_text('{"u": "hooks.slack.com"}', encoding="utf-8")
    (tmp_path / "ok.py").write_text("HOST = 'api.anthropic.com'\n", encoding="utf-8")
    rows = cs.scan_code("r", tmp_path)
    assert [r["kind"] for r in rows] == ["outbound-spend"]
    assert "TOKEN" not in json.dumps(rows)


def test_secret_filter_matches_whole_names_only(tmp_path):
    # A parent folder with "secret" in its name (pytest's own temp folders do
    # this) and ordinary files such as secretary/ and secretCore.ts are read.
    root = tmp_path / "test_secret_files_are_never_rea0"
    (root / "src" / "secretary").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "src" / "secretary" / "send.js").write_text(
        "fetch('https://api.telegram.org/x')\n", encoding="utf-8")
    (root / "lib" / "secretCore.ts").write_text("const u = 'hooks.slack.com'\n", encoding="utf-8")
    (root / "lib" / "id_rsa").write_text("api.openai.com\n", encoding="utf-8")
    kinds = sorted(r["title"] for r in cs.scan_code("r", root))
    assert kinds == [
        "Code can send messages through api.telegram.org",
        "Code can send messages through hooks.slack.com",
    ]
    assert cs._is_secret("src/secretary/send.js") is False
    assert cs._is_secret("config/credentials.yml") and cs._is_secret("deploy/server.pem")


def test_token_shaped_text_is_redacted(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": [
        "Bash(curl -H 'Authorization: Bearer abc123SECRETxyz' https://example.com/deploy)",
        "Bash(git push https://pat:hunter2@gitlab.example.com/x.git)",
        "Bash(deploy --token glpat-AAAA1111BBBB2222CCCC3333)",
        "Bash(git push:*)",
    ]}}), encoding="utf-8")
    dumped = json.dumps(cs.scan_tools("r", tmp_path))
    for leaked in ("abc123SECRETxyz", "hunter2", "glpat-AAAA1111BBBB2222CCCC3333"):
        assert leaked not in dumped
    assert "git push:*" in dumped and "[redacted]" in dumped
    # ordinary paths and folder names are left alone
    for plain in (
        "scripts/key-register/build.py:12",
        "projects/2025-01-15-some-long-project-folder-name/content.js:105",
        "scheduled-tasks/mm-v1-launch-day-reminder",
        "POLL_LINKEDIN_ANALYTICS_ACTIVE_CRON",
    ):
        assert cs._redact(plain) == plain
    assert "[redacted]" in cs._redact("x 0123456789abcdef0123456789abcdef0123 y")
    assert "[redacted]" in cs._redact("x Zx9Qw3Er7Ty1Ui5Op2As4Df6Gh8 y")


def test_schedule_call_split_over_lines(tmp_path):
    (tmp_path / "queue.js").write_text(
        "await boss.schedule(\n  'weekly-rollup',\n  '15 6 * * 1',\n  {});\n"
        "await boss.schedule(\n  NIGHTLY_JOB,\n  NIGHTLY_CRON,\n  {});\n", encoding="utf-8"
    )
    rows = cs.scan_code("r", tmp_path)
    assert [r["reach"] for r in rows] == [{"cron": "15 6 * * 1"}, {"cron": "NIGHTLY_CRON"}]


def test_render_yaml_auto_deploy(tmp_path):
    (tmp_path / "render.yaml").write_text(
        "services:\n  - autoDeploy: true\n    preDeployCommand: npm run db:migrate\n", encoding="utf-8"
    )
    row = cs.scan_deploy("r", tmp_path)[0]
    assert row["flag"] == "LIVE" and row["reach"] == {"auto": True, "pre_deploy": True}


def test_agent_tasks_outside_any_repo(tmp_path):
    task = tmp_path / "scheduled-tasks" / "monday-tidy"
    task.mkdir(parents=True)
    (task / "SKILL.md").write_text("Reconcile, then commit and push the ledger.\n", encoding="utf-8")
    rows = cs.scan_agent_tasks([str(tmp_path / "scheduled-tasks")])
    assert rows[0]["kind"] == "agent-task" and rows[0]["reach"]["acts"] == ["commit", "push"]


def test_diff_new_widened_drifted_not_found_and_retired(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text(THREE_MANUAL_JOBS, encoding="utf-8")
    first, _ = cs.scan(_cfg(tmp_path), fetch=False)
    base = cs.merge_baseline({}, first)
    by_title = {r["title"]: rid for rid, r in base.items()}
    a, c = by_title[_title("deploy-a")], by_title[_title("deploy-c")]
    base[a]["human"] = {"tier": 4, "involved": "Pat", "mark": "Covered", "confirmed": "2026-11-01"}
    base[c]["retired"] = True
    # deploy-a becomes automatic, deploy-b disappears, deploy-d is new,
    # deploy-c was retired and turns up again
    (tmp_path / ".gitlab-ci.yml").write_text(
        "deploy-a:\n  script:\n    - ./deploy.sh\n\n"
        "deploy-c:\n  script:\n    - ./deploy.sh\n\n"
        "deploy-d:\n  script:\n    - ./deploy.sh\n",
        encoding="utf-8",
    )
    second, _ = cs.scan(_cfg(tmp_path), fetch=False)
    ch = cs.diff(base, second, today=date(2026, 11, 16))
    assert sorted(r["title"] for r in ch["new"]) == [_title("deploy-c"), _title("deploy-d")]
    assert [r["id"] for r in ch["widened"]] == [a]
    assert [r["id"] for r in ch["drifted"]] == [a]
    assert [r["title"] for r in ch["not_found"]] == [_title("deploy-b")]
    assert ch["stale_claims"] == []


def test_merge_baseline_never_drops_a_row_or_its_human_half(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text(THREE_MANUAL_JOBS, encoding="utf-8")
    first, _ = cs.scan(_cfg(tmp_path), fetch=False)
    base = cs.merge_baseline({}, first, when="2026-11-01")
    b = {r["title"]: rid for rid, r in base.items()}[_title("deploy-b")]
    base[b]["human"] = {"tier": 4, "involved": "Pat", "mark": "Covered"}
    # the repo path goes missing, so the next scan finds nothing at all
    nothing, notes = cs.scan({"repos": [{"name": "r", "path": str(tmp_path / "gone")}]}, fetch=False)
    assert nothing == [] and notes
    merged = cs.merge_baseline(base, nothing, when="2026-11-16")
    assert set(merged) == set(base)
    assert merged[b]["human"]["involved"] == "Pat" and merged[b]["last_seen"] == "2026-11-01"
    again, _ = cs.scan(_cfg(tmp_path), fetch=False)
    assert cs.merge_baseline(merged, again, when="2026-11-17")[b]["last_seen"] == "2026-11-17"


def test_a_named_person_needs_a_recent_confirmed_date():
    base = {
        "x1": {"id": "x1", "repo": "r", "title": "t1", "human": {"involved": "Pat"}},
        "x2": {"id": "x2", "repo": "r", "title": "t2", "human": {"involved": "Pat", "confirmed": "2026-05-01"}},
        "x3": {"id": "x3", "repo": "r", "title": "t3", "human": {"involved": "Pat", "confirmed": "2026-11-01"}},
        "x4": {"id": "x4", "repo": "r", "title": "t4", "human": {"involved": "nobody"}},
    }
    ch = cs.diff(base, [], today=date(2026, 11, 16), max_age_days=90)
    assert sorted(r["id"] for r in ch["stale_claims"]) == ["x1", "x2"]


def test_a_jump_in_volume_reads_as_doing_more():
    old = {"reach": {"any": True, "agent_marked": True, "volume_band": cs._band(8)}}
    new = {"reach": {"any": True, "agent_marked": True, "volume_band": cs._band(386)}}
    assert cs._widened(old, new) == ["volume_band: 1 -> 3"]
    assert cs._widened(new, new) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_history_shows_direct_commits_merges_and_agent_marks(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", "-c", "user.name=Pat", "-c", "user.email=pat@example.com",
                        "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True)

    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git("init", "--bare", "-b", "master", str(remote), cwd=tmp_path)
    git("clone", str(remote), str(work), cwd=tmp_path)
    (work / "a.txt").write_text("1\n", encoding="utf-8")
    git("add", ".", cwd=work)
    git("commit", "-m", "direct change", cwd=work)
    git("checkout", "-b", "feature", cwd=work)
    (work / "b.txt").write_text("2\n", encoding="utf-8")
    git("add", ".", cwd=work)
    git("commit", "-m", "agent change\n\nCo-Authored-By: Claude <noreply@anthropic.com>", cwd=work)
    git("checkout", "master", cwd=work)
    git("merge", "--no-ff", "feature", "-m", "Merge branch 'feature'", cwd=work)
    git("push", "origin", "master", cwd=work)
    rows, notes = cs.scan_history("r", work, "master", 30, True, 30)
    by_kind = {r["kind"]: r for r in rows}
    assert "1 merges" in by_kind["history-merge"]["trigger"]
    assert by_kind["history-merge"]["reach"]["agent_marked"] is True
    assert by_kind["history-merge"]["reach"]["volume_band"] == 1
    assert "1 direct commits" in by_kind["history-direct"]["trigger"]
    assert by_kind["history-merge"]["history_actor"].startswith("Pat")
    assert "stale-copy" not in by_kind and notes == []


def test_report_always_states_the_limits():
    md = cs.report_md([], [], None)
    assert "Keys and passwords are not scanned" in md and "not a grade" in md
