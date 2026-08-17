# DreamTeam Organization-wide GitHub Configuration

This repository contains organization-wide GitHub configuration and reusable workflows for all DreamTeam repositories.

## Reusable Workflows

### Monday Item Connection Check

**File**: `.github/workflows/monday-item-check.yml`

This workflow enforces SOC2 compliance by requiring all PRs to have a two-way connection to Monday.com tickets:

1. **Branch name** must contain a Monday Item ID (8+ digits)
2. **PR description** must contain a Monday.com ticket link

#### Usage

Add this workflow to any repository by creating `.github/workflows/monday-check.yml`:

```yaml
name: Monday Item Check

on:
  pull_request:
    branches: [master, main]

jobs:
  monday-check:
    uses: dreamteamapp/.github/.github/workflows/monday-item-check.yml@master
```

#### Branch Naming Convention

```
feature/<monday-item-id>-<description>
```

**Examples:**
- `feature/1234567890-add-user-authentication`
- `feature/9876543210-fix-database-connection`

#### PR Description Requirement

The PR description must include a link to the Monday ticket:

```markdown
🔗 **Monday Ticket**: [1234567890](https://dreamteam.monday.com/boards/xxxxx/pulses/1234567890)
```

### Code Review Attestation Check

**File**: `.github/workflows/code-review-attestation-check.yml`

The review counterpart of the Monday check. It requires a PR to carry evidence that a code review was run on it, as one line in the PR description:

```
<!-- code-review: sha=a3f9c21 findings=3 blockers=0 at=2026-08-17T10:04Z skill=review-pr -->
```

The Shapes review skills write that line, by two routes. `review-pr` runs against an open PR and edits the description directly. `code-review` runs before a PR exists, so it leaves the line in a note under `.git/` that `create-pr-with-monday-item` reads into the body it writes — which is what stops a review that ran in an earlier session from being lost. A human can also add the line by hand: the failure summary prints the exact one to paste, with the current head SHA already filled in.

It reads text out of the PR and nothing else. It does not run a review and never judges what a review found.

| Situation | Result |
|---|---|
| Marker present, `sha` matches head | pass |
| Marker present, head moved since | pass, with a warning |
| No marker, or a malformed one | **fail** |
| Marker only inside a code block | **fail** — that is an example, not an attestation |
| `no-review-needed` label | pass, exemption recorded in the PR timeline |
| Bot-authored PR | pass |
| `merge_group` ref | pass |
| PR opened before the repo's `enforce-from` | pass, grandfathered |
| `enforce-from` set to something unreadable | **fail** — the repo's caller is misconfigured |

#### Usage

Create `.github/workflows/code-review-check.yml`:

```yaml
name: Code Review Check

on:
  pull_request:
    types: [opened, edited, synchronize, reopened, labeled, unlabeled]
  merge_group:

permissions: {}

jobs:
  code-review-check:
    uses: dreamteamapp/.github/.github/workflows/code-review-attestation-check.yml@master
```

Three details are load-bearing, and each has a named failure mode:

1. **The job id must be `code-review-check`.** The required-check context is `<job id> / Verify Code Review Attestation`, and a ruleset names exactly one string. Rename the job and the repo silently drops out of the gate while still showing a green check.
2. **Keep `edited`.** It is what makes the check clear itself the moment the attestation is pasted into the body. Without it the author has no way forward but a dummy push. This exact bug shipped once already on the Monday caller (`5e33cb7`).
3. **Keep `merge_group`.** A merge queue re-runs required checks against a ref with no PR attached; the reusable workflow short-circuits that to a pass, but only if it is invoked at all, and a required check that never reports blocks the merge forever.

`exempt-label` is configurable via `with:` if a repo needs a different label name.

#### Adopting in a repo that already has open PRs

A required status check applies to every **open** pull request the moment it becomes required, not only to new ones. A repo with a long-lived PR backlog therefore has every one of those PRs blocked on a review nobody is going to retro-fit, and the usual outcome is that someone asks for the check to be switched off.

`enforce-from` is the cutoff. PRs opened before it report green as grandfathered:

```yaml
jobs:
  code-review-check:
    uses: dreamteamapp/.github/.github/workflows/code-review-attestation-check.yml@master
    with:
      enforce-from: "2026-08-18"
```

Accepts `YYYY-MM-DD` (midnight UTC) or a full `YYYY-MM-DDTHH:MM:SSZ`. Two things to know:

- A grandfathered PR stays ungated for life, however many commits it gains. That is the price of not wedging the backlog. Delete the input once the repo's old PRs have drained and the repo is fully gated from then on.
- A cutoff the check cannot parse **fails the check** rather than being ignored, because ignoring it would exempt every PR in the repo silently.

A repo with no PR backlog should skip the input entirely and gate everything.

#### Also create the label

```bash
gh label create no-review-needed --repo dreamteamapp/<repo> --color FBCA04 \
  --description "This change genuinely does not warrant a code review; exempts it from the review attestation check"
```

Without it, `gh pr edit --add-label` fails and the exemption path is unreachable.

## Adding to a New Repository

1. Create `.github/workflows/monday-check.yml` and `.github/workflows/code-review-check.yml` in your repository
2. Add the workflow configurations shown above
3. Create the `no-review-needed` label
4. Configure branch protection to require these checks to pass before merging

## Testing These Workflows

`tests/` holds tests for the workflows here, run by `.github/workflows/test-workflows.yml`. They pull each step's shell straight out of the shipped YAML and execute it, so a test cannot drift from the file that deploys, and they pin the GitHub-expression layer (step `if:` conditions, job ids, trigger lists) with exact string assertions, since there is no way to evaluate a GitHub expression locally.

This matters more here than in a normal repository: every repo in the org consumes these files, a defect breaks all of their CI at once, and a reusable workflow cannot be run locally at all.

```bash
python3 -m pip install pyyaml
python3 tests/test_code_review_attestation_check.py
```

## SOC2 Compliance

These workflows help maintain SOC2 Type 2 compliance by ensuring:
- All code changes are traceable to approved tickets
- Two-way linkage between tickets and PRs
- Automated enforcement (cannot be bypassed)

