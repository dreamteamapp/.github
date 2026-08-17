#!/usr/bin/env python3
"""Exercise the Decide step of code-review-attestation-check.yml.

Pulls the step's `run:` script straight out of the workflow so the test cannot
drift from the file that ships, then runs it under a fake $GITHUB_OUTPUT for
every branch of the verdict.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

WF = Path(__file__).parent.parent / ".github/workflows/code-review-attestation-check.yml"

doc = yaml.safe_load(WF.read_text())
job = doc["jobs"]["check-review-attestation"]
steps = {s.get("id") or s["name"]: s for s in job["steps"]}
script = steps["decide"]["run"]

DEFAULTS = {
    "EVENT_NAME": "pull_request",
    "PR_BODY": "",
    "HEAD_SHA": "d0e3576f8aa1b2c3d4e5f60718293a4b5c6d7e8f",
    "AUTHOR_LOGIN": "danibekman",
    "AUTHOR_TYPE": "User",
    "HAS_EXEMPT_LABEL": "false",
    "EXEMPT_LABEL": "no-review-needed",
    "EXEMPT_AUTHORS": "",
}

GOOD = "<!-- code-review: sha=d0e3576 lanes=7 findings=3 blockers=0 at=2026-08-17T10:04Z skill=quad-plus-review -->"

# The exact shapes the emitters in dreamteamapp/shapes-knowledge write. These are
# a cross-repo contract: the skills live in another repository and cannot import
# this regex, so the format they document is pinned here instead. A change to
# either side that is not made on both breaks the gate for everyone.
EMITTED = [
    "<!-- code-review: sha=d0e3576 findings=3 blockers=0 at=2026-08-17T10:04Z skill=review-pr -->",
    "<!-- code-review: sha=d0e3576 findings=0 blockers=0 at=2026-08-17T10:04Z skill=code-review -->",
]

CASES = [
    ("merge queue ref",              {"EVENT_NAME": "merge_group"},                              "exempt_merge_group"),
    ("merge queue beats a bad body", {"EVENT_NAME": "merge_group", "PR_BODY": "no marker here"},  "exempt_merge_group"),
    ("bot by user.type",             {"AUTHOR_TYPE": "Bot", "AUTHOR_LOGIN": "github-actions[bot]"}, "exempt_bot"),
    ("bot by login suffix only",     {"AUTHOR_LOGIN": "shapes-automation[bot]"},                  "exempt_bot"),
    ("configured exempt author",     {"AUTHOR_LOGIN": "release-cron", "EXEMPT_AUTHORS": "foo, release-cron"}, "exempt_bot"),
    ("exempt author no match",       {"AUTHOR_LOGIN": "danibekman", "EXEMPT_AUTHORS": "foo,bar"},  "fail_missing"),
    ("exempt label",                 {"HAS_EXEMPT_LABEL": "true"},                                "exempt_label"),
    ("label beats a missing marker", {"HAS_EXEMPT_LABEL": "true", "PR_BODY": "nothing"},          "exempt_label"),
    ("no body at all",               {},                                                          "fail_missing"),
    ("body without a marker",        {"PR_BODY": "## What\nFixes the thing.\n"},                  "fail_missing"),
    ("marker, no sha",               {"PR_BODY": "<!-- code-review: lanes=7 -->"},                "fail_malformed"),
    ("marker, sha not hex",          {"PR_BODY": "<!-- code-review: sha=zzzzzzz -->"},            "fail_malformed"),
    ("marker, sha too short",        {"PR_BODY": "<!-- code-review: sha=d0e35 -->"},              "fail_malformed"),
    ("marker not closed on a line",  {"PR_BODY": "<!-- code-review: sha=d0e3576\n     at=now -->"}, "fail_malformed"),
    ("good marker, current head",    {"PR_BODY": GOOD},                                           "pass"),
    ("good marker in a real body",   {"PR_BODY": f"## Why\nBecause.\n\n{GOOD}\n"},                "pass"),
    ("full 40-char sha",             {"PR_BODY": "<!-- code-review: sha=d0e3576f8aa1b2c3d4e5f60718293a4b5c6d7e8f -->"}, "pass"),
    ("uppercase sha",                {"PR_BODY": "<!-- code-review: SHA=D0E3576 -->"},            "pass"),
    ("extra inner whitespace",       {"PR_BODY": "<!--   code-review:   sha=d0e3576   -->"},      "pass"),
    ("stale sha",                    {"PR_BODY": "<!-- code-review: sha=a3f9c21 -->"},            "pass_stale"),
    ("first marker wins",            {"PR_BODY": "<!-- code-review: sha=a3f9c21 -->\n<!-- code-review: sha=d0e3576 -->"}, "pass_stale"),
    # The PR body is attacker-controlled text. It must never reach a shell.
    ("body attempts injection",      {"PR_BODY": '$(touch /tmp/pwned-cra) `id` ${IFS} "; touch /tmp/pwned2-cra; #'}, "fail_missing"),
    ("marker plus injection",        {"PR_BODY": f'{GOOD}\n$(touch /tmp/pwned3-cra)'},            "pass"),
] + [
    (f"emitted by {m.split('skill=')[1].split()[0]}", {"PR_BODY": m}, "pass") for m in EMITTED
]


def run(overrides):
    env = dict(os.environ)
    env.update(DEFAULTS)
    env.update(overrides)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"
        out.write_text("")
        env["GITHUB_OUTPUT"] = str(out)
        sh = Path(d) / "decide.sh"
        sh.write_text(script)
        proc = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True)
        outputs = {}
        for line in out.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        return proc, outputs


SUMMARY_ENV = {
    "ATTESTED_SHA": "d0e3576",
    "ATTESTED_SKILL": "quad-plus-review",
    "EXEMPT_AUTHOR": "github-actions[bot]",
    "HEAD_SHA": DEFAULTS["HEAD_SHA"],
    "EXEMPT_LABEL": "no-review-needed",
}


def run_summary(verdict):
    """Every verdict must render without a shell error — a broken summary step
    fails the job on a PR the gate had already decided to pass."""
    summary_script = steps["Generate Summary"]["run"]
    env = dict(os.environ)
    env.update(SUMMARY_ENV)
    env["VERDICT"] = verdict
    with tempfile.TemporaryDirectory() as d:
        s = Path(d) / "summary.md"
        s.write_text("")
        env["GITHUB_STEP_SUMMARY"] = str(s)
        sh = Path(d) / "summary.sh"
        sh.write_text(summary_script)
        proc = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True)
        return proc, s.read_text()


def main():
    for canary in ("/tmp/pwned-cra", "/tmp/pwned2-cra", "/tmp/pwned3-cra"):
        Path(canary).unlink(missing_ok=True)

    failures = []

    print("job name:", repr(job["name"]))
    if job["name"] != "Verify Code Review Attestation":
        failures.append(("job name changed — the required-check context would move", "Verify Code Review Attestation", job["name"], 0, "", ""))
    if job.get("if") is not None:
        failures.append(("job carries an `if:` — a skipped required check never reports success", "no if", str(job["if"]), 0, "", ""))

    for verdict in ("pass", "pass_stale", "exempt_label", "exempt_bot", "exempt_merge_group", "fail_missing", "fail_malformed"):
        proc, rendered = run_summary(verdict)
        ok = proc.returncode == 0 and len(rendered.strip()) > 40
        print(f"{'PASS' if ok else 'FAIL'}  summary renders: {verdict}")
        if not ok:
            failures.append((f"summary {verdict}", "renders", f"rc={proc.returncode}", proc.returncode, proc.stdout, proc.stderr))
    print()
    for name, overrides, expected in CASES:
        proc, outputs = run(overrides)
        got = outputs.get("verdict", "<none>")
        ok = proc.returncode == 0 and got == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name:32} -> {got}")
        if not ok:
            failures.append((name, expected, got, proc.returncode, proc.stdout, proc.stderr))

    for canary in ("/tmp/pwned-cra", "/tmp/pwned2-cra", "/tmp/pwned3-cra"):
        if Path(canary).exists():
            failures.append((f"INJECTION: {canary} was created", "no file", "file exists", 0, "", ""))
            Path(canary).unlink()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f[0]}: expected {f[1]}, got {f[2]} (rc={f[3]})")
            if f[4].strip():
                print(f"      stdout: {f[4].strip()[:400]}")
            if f[5].strip():
                print(f"      stderr: {f[5].strip()[:400]}")
        return 1
    print(f"All {len(CASES)} cases pass, and no injection canary fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
