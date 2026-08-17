#!/usr/bin/env python3
"""Exercise code-review-attestation-check.yml and its caller.

Every script under test is pulled straight out of the shipped YAML, so a test
cannot drift from the file that deploys. That covers the shell layer faithfully.

It does NOT cover the GitHub-expression layer: everything between `${{` and `}}`
is evaluated by GitHub, not by bash, and there is no local interpreter for it.
Both mutations that disable this gate entirely live in that layer — a typo in the
Fail step's `if:`, and a new failing verdict that does not begin with `fail`. So
that layer is pinned with exact string assertions on the YAML instead. A string
assertion is not a behavioural one; for a layer with no interpreter it is the
strongest guard available, and it kills the mutation.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WF = ROOT / ".github/workflows/code-review-attestation-check.yml"
CALLER = ROOT / ".github/workflows/code-review-check.yml"

doc = yaml.safe_load(WF.read_text())
job = doc["jobs"]["check-review-attestation"]
steps = {s.get("id") or s["name"]: s for s in job["steps"]}
script = steps["decide"]["run"]

# The verdict vocabulary, split by the polarity the Fail step's `if:` depends on.
FAILING = {"fail_missing", "fail_malformed", "fail_example"}
GREEN = {"pass", "pass_stale", "exempt_label", "exempt_bot", "exempt_merge_group"}

HEAD = "d0e3576f8aa1b2c3d4e5f60718293a4b5c6d7e8f"

DEFAULTS = {
    "EVENT_NAME": "pull_request",
    "PR_BODY": "",
    "HEAD_SHA": HEAD,
    "AUTHOR_LOGIN": "danibekman",
    "AUTHOR_TYPE": "User",
    "HAS_EXEMPT_LABEL": "false",
    "EXEMPT_LABEL": "no-review-needed",
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

EXPECTED_HEADING = {
    "pass": "### ✅ PASSED",
    "pass_stale": "### ✅ PASSED",
    "exempt_label": "### ✅ EXEMPT — label",
    "exempt_bot": "### ✅ EXEMPT — automated PR",
    "exempt_merge_group": "### ✅ EXEMPT — merge queue",
    "fail_missing": "### ❌ FAILED — no code review attestation",
    "fail_malformed": "### ❌ FAILED — attestation is malformed",
    "fail_example": "### ❌ FAILED — that marker is an example, not an attestation",
}

CASES = [
    ("merge queue ref",              {"EVENT_NAME": "merge_group"},                              "exempt_merge_group"),
    # Precedence over a verdict the default empty body cannot reach, so this
    # proves the short-circuit runs BEFORE the sanitizer and the malformed check.
    ("merge queue beats malformed",  {"EVENT_NAME": "merge_group", "PR_BODY": "<!-- code-review: lanes=7 -->"}, "exempt_merge_group"),
    ("bot by user.type",             {"AUTHOR_TYPE": "Bot", "AUTHOR_LOGIN": "github-actions[bot]"}, "exempt_bot"),
    ("bot by login suffix only",     {"AUTHOR_LOGIN": "shapes-automation[bot]"},                  "exempt_bot"),
    ("exempt label",                 {"HAS_EXEMPT_LABEL": "true"},                                "exempt_label"),
    ("label beats an example",       {"HAS_EXEMPT_LABEL": "true", "PR_BODY": f"```\n{GOOD}\n```"}, "exempt_label"),
    ("no body at all",               {},                                                          "fail_missing"),
    ("body without a marker",        {"PR_BODY": "## What\nFixes the thing.\n"},                  "fail_missing"),
    ("marker, no sha",               {"PR_BODY": "<!-- code-review: lanes=7 -->"},                "fail_malformed"),
    ("marker, sha not hex",          {"PR_BODY": "<!-- code-review: sha=zzzzzzz -->"},            "fail_malformed"),
    ("marker, sha too short",        {"PR_BODY": "<!-- code-review: sha=d0e35 -->"},              "fail_malformed"),
    ("marker not closed on a line",  {"PR_BODY": "<!-- code-review: sha=d0e3576\n     at=now -->"}, "fail_malformed"),
    ("good marker, current head",    {"PR_BODY": GOOD},                                           "pass"),
    ("good marker in a real body",   {"PR_BODY": f"## Why\nBecause.\n\n{GOOD}\n"},                "pass"),
    ("full 40-char sha",             {"PR_BODY": f"<!-- code-review: sha={HEAD} -->"},            "pass"),
    ("uppercase sha",                {"PR_BODY": "<!-- code-review: SHA=D0E3576 -->"},            "pass"),
    ("extra inner whitespace",       {"PR_BODY": "<!--   code-review:   sha=d0e3576   -->"},      "pass"),
    ("stale sha",                    {"PR_BODY": "<!-- code-review: sha=a3f9c21 -->"},            "pass_stale"),
    ("first marker wins",            {"PR_BODY": "<!-- code-review: sha=a3f9c21 -->\n<!-- code-review: sha=d0e3576 -->"}, "pass_stale"),
    ("empty head sha is stale",      {"PR_BODY": GOOD, "HEAD_SHA": ""},                           "pass_stale"),
    # GitHub renders contains() as lowercase `true`; anything else is not exempt.
    ("label flag must be lowercase", {"HAS_EXEMPT_LABEL": "True"},                                "fail_missing"),
    ("body attempts injection",      {"PR_BODY": '$(touch /tmp/pwned-cra) `id` ${IFS} "; touch /tmp/pwned2-cra; #'}, "fail_missing"),
    ("marker plus injection",        {"PR_BODY": f'{GOOD}\n$(touch /tmp/pwned3-cra)'},            "pass"),
    # --- A documented example is not an attestation. -----------------------
    # Regression: the first live run of this check PASSED on the PR that added
    # it, matching the sample marker in its own description. A repo PR template
    # carrying an example would have done that to every PR in the repo.
    ("example in a ``` fence",       {"PR_BODY": f"Format:\n\n```\n{GOOD}\n```\n"},               "fail_example"),
    ("example in a ~~~ fence",       {"PR_BODY": f"Format:\n\n~~~text\n{GOOD}\n~~~\n"},           "fail_example"),
    ("example in a ```yaml fence",   {"PR_BODY": f"```yaml\n{GOOD}\n```"},                        "fail_example"),
    ("example in inline backticks",  {"PR_BODY": f"Add `{GOOD}` to the body."},                   "fail_example"),
    ("real marker beats an example", {"PR_BODY": f"```\n{GOOD}\n```\n\n{GOOD}\n"},                "pass"),
    ("unclosed fence fails closed",  {"PR_BODY": f"```\nsomething\n\n{GOOD}\n"},                  "fail_example"),
    # An inline span is delimited by a RUN of backticks of equal length. Matching
    # exactly one per side stripped odd runs and left even ones intact, so a
    # marker in double backticks came through as a real attestation.
    ("example in 2 backticks",       {"PR_BODY": f"Add `` {GOOD} `` to the body."},               "fail_example"),
    ("example in 4 backticks",       {"PR_BODY": f"Add ```` {GOOD} ```` to the body."},           "fail_example"),
    ("example in 6 backticks",       {"PR_BODY": f"Add `````` {GOOD} `````` to the body."},       "fail_example"),
    # A fence closes only on the same char at the same length or longer. Toggling
    # on any run of three treated the inner ``` of a ````-wrapped example as a
    # close, reopened the body mid-example, and returned the marker as real.
    ("4-fence wrapping a 3-fence",   {"PR_BODY": f"````\n```\n{GOOD}\n```\n````\n"},              "fail_example"),
    ("~~~ inside a ``` fence",       {"PR_BODY": f"```\n~~~\n{GOOD}\n```\n"},                     "fail_example"),
    ("``` inside a ~~~ fence",       {"PR_BODY": f"~~~\n```\n{GOOD}\n~~~\n"},                     "fail_example"),
    # CRLF is what the GitHub API actually returns for a PR body.
    ("crlf body, real marker",       {"PR_BODY": f"## Why\r\nBecause.\r\n\r\n{GOOD}\r\n"},        "pass"),
    ("crlf body, fenced example",    {"PR_BODY": f"```\r\n{GOOD}\r\n```\r\n"},                    "fail_example"),
    # --- Known gaps, pinned so the answer is on the record rather than a --
    # --- surprise. Markdown has more ways to quote a line than a fence and --
    # --- an inline span; these are accepted, not overlooked. ----------------
    ("KNOWN GAP indented block",     {"PR_BODY": f"Example:\n\n    {GOOD}\n"},                    "pass"),
    ("KNOWN GAP blockquote",         {"PR_BODY": f"> {GOOD}\n"},                                  "pass"),
    ("KNOWN GAP pre tag",            {"PR_BODY": f"<pre>{GOOD}</pre>"},                           "pass"),
]


def _emitter_name(marker):
    """Label an EMITTED case by its skill, without dying at import if absent."""
    found = re.search(r"skill=([\w.-]+)", marker)
    return found.group(1) if found else "unspecified"


CASES += [(f"emitted by {_emitter_name(m)}", {"PR_BODY": m}, "pass") for m in EMITTED]

CANARIES = ("/tmp/pwned-cra", "/tmp/pwned2-cra", "/tmp/pwned3-cra")


def _env(overrides, keys_from=DEFAULTS):
    """Minimal explicit environment.

    Deliberately NOT `dict(os.environ)`: inheriting the developer's shell means a
    green local run is not evidence of a green CI run. LC_ALL is pinned because
    the script's `grep -oiE` and `[[:space:]]` behaviour on non-ASCII bodies is
    locale-dependent.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"}
    env.update(keys_from)
    env.update(overrides)
    return env


def _run_step(step_key, overrides, keys_from=DEFAULTS, want_summary=False):
    """Run one step's shell, returning (CompletedProcess, outputs, summary_text).

    `outputs` is the parsed `$GITHUB_OUTPUT` the step wrote; `summary_text` is
    the `$GITHUB_STEP_SUMMARY` it rendered (empty unless want_summary).
    """
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"
        out.write_text("")
        summary = Path(d) / "summary.md"
        summary.write_text("")
        env = _env(overrides, keys_from)
        env["GITHUB_OUTPUT"] = str(out)
        env["GITHUB_STEP_SUMMARY"] = str(summary)
        sh = Path(d) / "step.sh"
        sh.write_text(steps[step_key]["run"])
        proc = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True)
        outputs = {}
        for line in out.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        return proc, outputs, (summary.read_text() if want_summary else "")


def run(overrides):
    proc, outputs, _ = _run_step("decide", overrides)
    return proc, outputs


def check(cond, label, failures, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append((label, detail))
    return cond


def structural_checks(failures):
    """Pin the GitHub-expression layer, which no local run can exercise."""
    check(job["name"] == "Verify Code Review Attestation",
          "job name is the required-check context", failures, repr(job.get("name")))
    check(job.get("if") is None,
          "job has no `if:` (a skipped required check never reports success)", failures, str(job.get("if")))

    # The gate expression, verbatim. A one-character typo here disables the gate
    # on every consuming repo while every other test stays green.
    check(steps["fail"]["if"] == "startsWith(steps.decide.outputs.verdict, 'fail')",
          "fail step gate expression is exact", failures, repr(steps["fail"].get("if")))

    # The verdict vocabulary and the gate's polarity are one fact. A new failing
    # verdict that does not begin with `fail` would render a red summary and
    # report the job green.
    emitted = set(re.findall(r"^\s*emit\s+([a-z_]+)", script, re.M))
    check(emitted == FAILING | GREEN, "verdict vocabulary matches the test's own", failures, str(emitted))
    check(all(v.startswith("fail") for v in FAILING), "every failing verdict starts with 'fail'", failures)
    check(not any(v.startswith("fail") for v in GREEN), "no green verdict starts with 'fail'", failures)
    check(set(EXPECTED_HEADING) == FAILING | GREEN, "every verdict has an asserted heading", failures)

    # Fixture-vs-contract mirrors. Renaming a declared env key in the YAML alone
    # kills the step under `set -u` on the first PR in every consuming repo.
    check(set(DEFAULTS) == set(steps["decide"]["env"]),
          "decide env fixture mirrors the step's env:", failures,
          f"{set(DEFAULTS) ^ set(steps['decide']['env'])}")
    check({"VERDICT", "EXEMPT_LABEL"} == set(steps["fail"]["env"]),
          "fail env fixture mirrors the step's env:", failures, str(set(steps["fail"]["env"])))
    check(doc[True]["workflow_call"]["inputs"]["exempt-label"]["default"] == DEFAULTS["EXEMPT_LABEL"],
          "exempt-label default matches the documented label", failures)

    # The caller is what ~26 repos copy, and each property has a named failure
    # mode. Missing `edited` already shipped once in this repo (5e33cb7).
    caller = yaml.safe_load(CALLER.read_text())
    on = caller[True]  # YAML 1.1 parses the `on:` key as the boolean True
    check(list(caller["jobs"]) == ["code-review-check"],
          "caller job id is the required-check context", failures, str(list(caller["jobs"])))
    check("merge_group" in on, "caller declares merge_group", failures, str(list(on)))
    check({"opened", "edited", "synchronize", "reopened", "labeled", "unlabeled"} <= set(on["pull_request"]["types"]),
          "caller declares every trigger the remediation paths need", failures, str(on["pull_request"]["types"]))


def summary_checks(failures):
    """Render each verdict from the outputs a real Decide run produced for it."""
    for verdict, overrides in (
        ("pass", {"PR_BODY": GOOD}),
        ("pass_stale", {"PR_BODY": "<!-- code-review: sha=a3f9c21 -->"}),
        ("exempt_label", {"HAS_EXEMPT_LABEL": "true"}),
        ("exempt_bot", {"AUTHOR_TYPE": "Bot", "AUTHOR_LOGIN": "github-actions[bot]"}),
        ("exempt_merge_group", {"EVENT_NAME": "merge_group"}),
        ("fail_missing", {}),
        ("fail_malformed", {"PR_BODY": "<!-- code-review: lanes=7 -->"}),
        ("fail_example", {"PR_BODY": f"```\n{GOOD}\n```"}),
    ):
        _, outputs = run(overrides)
        if outputs.get("verdict") != verdict:
            check(False, f"summary fixture reaches {verdict}", failures, str(outputs))
            continue
        # Exactly the env a real run leaves behind, empty strings included.
        env = {
            "VERDICT": verdict,
            "ATTESTED_SHA": outputs.get("sha", ""),
            "ATTESTED_SKILL": outputs.get("skill", ""),
            "EXEMPT_AUTHOR": outputs.get("author", ""),
            "HEAD_SHA": overrides.get("HEAD_SHA", HEAD),
            "EXEMPT_LABEL": "no-review-needed",
        }
        proc, _, rendered = _run_step("summary", {}, keys_from=env, want_summary=True)
        ok = proc.returncode == 0 and EXPECTED_HEADING[verdict] in rendered
        if verdict.startswith("fail"):
            ok = ok and "To fix, pick one" in rendered
        else:
            # A passing PR must never render failure prose. This is what catches
            # a mutated `case` label that routes a green verdict to the catch-all.
            ok = ok and "❌" not in rendered and "To fix, pick one" not in rendered
        if verdict == "pass_stale":
            ok = ok and "The head has moved since this review" in rendered
        elif verdict == "pass":
            ok = ok and "The attestation covers the current head." in rendered
        check(ok, f"summary renders {verdict}", failures, proc.stderr.strip()[:300] or rendered[:300])


def fail_step_checks(failures):
    """The gate's whole purpose: a failing verdict must exit non-zero."""
    for verdict in sorted(FAILING):
        proc, _, _ = _run_step("fail", {}, keys_from={"VERDICT": verdict, "EXEMPT_LABEL": "no-review-needed"})
        check(proc.returncode == 1, f"fail step exits 1 on {verdict}", failures,
              f"rc={proc.returncode} {proc.stderr.strip()[:200]}")


def main():
    for canary in CANARIES:
        Path(canary).unlink(missing_ok=True)

    failures = []
    structural_checks(failures)
    print()
    fail_step_checks(failures)
    print()
    summary_checks(failures)
    print()

    for name, overrides, expected in CASES:
        proc, outputs = run(overrides)
        got = outputs.get("verdict", "<none>")
        ok = proc.returncode == 0 and got == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name:32} -> {got}")
        if not ok:
            failures.append((name, f"expected {expected}, got {got}, rc={proc.returncode} {proc.stderr.strip()[:300]}"))

    for canary in CANARIES:
        if Path(canary).exists():
            failures.append((f"INJECTION: {canary} was created", "the PR body reached a shell"))
            Path(canary).unlink()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for name, detail in failures:
            print(f"  - {name}: {detail}")
        return 1
    print(f"All checks pass ({len(CASES)} decision cases), and no injection canary fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
