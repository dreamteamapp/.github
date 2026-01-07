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
    uses: dreamteamapp/.github/.github/workflows/monday-item-check.yml@main
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

## Adding to a New Repository

1. Create `.github/workflows/monday-check.yml` in your repository
2. Add the workflow configuration shown above
3. Configure branch protection to require this check to pass before merging

## SOC2 Compliance

These workflows help maintain SOC2 Type 2 compliance by ensuring:
- All code changes are traceable to approved tickets
- Two-way linkage between tickets and PRs
- Automated enforcement (cannot be bypassed)

