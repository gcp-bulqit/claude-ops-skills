---
name: merge-train
description: Drive your open PRs into development one at a time — mark ready, sync, renumber drizzle migrations, wait for green CI, hold while you merge.
disable-model-invocation: true
---

# merge-train

Run the user's open PRs into `development` through to merge, oldest first, one at a time. The user merges; everything else is yours.

Every PR's turn ends in exactly one of two states:

- **merged** — the user merged it, move to the next
- **parked** — something needs the user, the train moves on without it

Parking is always the fallback. Never halt the train because one PR is unhappy.

## Preflight

Stop and report if any of these fail:

```bash
gh auth status
gh repo view --json nameWithOwner   # expect Bulqit/bul-website
git fetch origin development
```

Then ensure the reusable worktree exists at `.claude/worktrees/merge-train`:

```bash
git worktree add .claude/worktrees/merge-train origin/development 2>/dev/null \
  || git -C .claude/worktrees/merge-train rev-parse --git-dir
```

If the worktree exists but is unusable (missing, corrupt, detached in a state you cannot reset), remove it with `git worktree remove --force` and add it again.

## Build the queue

```bash
gh pr list --author @me --state open --base development \
  --json number,title,isDraft --jq 'sort_by(.number)'
```

Oldest PR number first — the oldest work merges first, so the youngest branches absorb the renumbering churn.

Print the queue, then work it in order.

## Per-PR loop

Run these steps for each PR. Print a one-line status on every state change.

### 1. Reset the worktree

All git work happens in `.claude/worktrees/merge-train`. Never touch the user's main checkout.

```bash
cd .claude/worktrees/merge-train
git merge --abort 2>/dev/null; git rebase --abort 2>/dev/null
git reset --hard
git clean -fd -e node_modules -e .turbo -e dist -e .next
git fetch origin development "$BRANCH"
git checkout -B "$BRANCH" "origin/$BRANCH"
```

`node_modules` and build caches survive so each PR does not pay a fresh install.

### 2. Mark ready

```bash
gh pr ready "$PR"
```

Only if the PR is a draft. Say that you did it.

### 3. Sync with development

```bash
git merge origin/development
```

Always merge, every PR, whether or not GitHub reports a conflict — a stale migration number produces no textual conflict but still breaks the migration run.

Never rebase and never force-push: other agents hold ~30 worktrees on these branches.

### 4. Renumber migrations

From inside the worktree, run the copy in the **main checkout** — the PR's branch may predate this skill, so the worktree's own `.claude/skills` may not have it:

```bash
node "$REPO_ROOT/.claude/skills/merge-train/scripts/renumber-migration.mjs"
```

Run it after every merge, conflicted or clean. It is idempotent: rerunning exits `0` with `numbering already correct, nothing to do`.

It rebuilds `packages/database/migrations` as *development's* migrations plus this PR's own, renumbered onto the end: files renamed, `meta/NNNN_snapshot.json` renamed where one exists, and each entry's `idx`, `tag` **and `when`** rewritten. The `when` bump is the part that matters — `packages/database/migrate.js` selects pending migrations by `entry.when > MAX(created_at)`, so a migration renumbered without a fresh `when` sits below the high-water mark and is silently never applied.

The script refuses to act and exits non-zero when the conflict is not a clean renumber (this branch modified an existing migration, or a conflicted path is not a migration file). Treat that refusal like step 5.

### 5. Park anything that is not a migration conflict

If conflicts remain after step 4:

```bash
git merge --abort
```

Leave the branch exactly as it was, park the PR naming the conflicting files, and move to the next PR.

### 6. Verify, commit, push

The script leaves its own static checks passing: filename number equals `idx` equals `tag` prefix, appended entries strictly ascending in both `idx` and `when`, no duplicate `idx` or `tag`, every renamed file present. Pre-existing drift elsewhere in the journal is not this skill's business — leave it alone and say nothing about it.

```bash
git commit -m "chore(db): renumber migration 0554 -> 0557"
git push origin "$BRANCH"
```

Commit messages are plain. No `Co-Authored-By`, no generated-with footer, no agent attribution anywhere — the repo's PR template forbids it.

**Push rejected** — another worktree agent moved the branch. Re-fetch, redo steps 1–6 against the new tip, push again. Rejected a second time: park the PR, something else is actively working on it.

### 7. Wait for green

```bash
gh pr checks "$PR" --watch --interval 30
```

**Green** means all four checks pass — `Lint`, `Typecheck`, `Test`, `Integration Tests (notifications)` — *and* `mergeStateStatus` is `CLEAN`:

```bash
gh pr view "$PR" --json mergeStateStatus,statusCheckRollup
```

No timeout: poll for as long as CI takes.

If the PR reports zero checks after ~5 minutes, the workflow never fired. Dispatch it and keep polling:

```bash
gh workflow run ci.yml --ref "$BRANCH"
```

If checks still do not attach to the PR after that, say so plainly and park it — a dispatched run is a branch run, so branch protection stays unsatisfied and the PR cannot be merged on its strength.

A genuine check failure is not yours to fix. Report which job failed with a link to the run, and ask whether to park or halt. Default to parking.

### 8. Hold for the manual merge

Print `⏸ PR #123 is green and conflict-free — merge it when you're ready`, then poll every 30 seconds. Run this in the background and wait for it to finish:

```bash
while :; do
  s=$(gh pr view "$PR" --json state,isDraft,labels \
        -q '[.state,(.isDraft|tostring),([.labels[].name]|join(","))]|join("|")')
  case "$s" in
    MERGED*|CLOSED*|*"|true|"*|*skip*) echo "$s"; break ;;
  esac
  sleep 30
done
```

Four exits, no timeout:

| Signal | Outcome |
|---|---|
| Merged | **merged** — advance |
| Closed | **parked** — the user gave up on it |
| Converted back to draft | **parked** — the user wants more work on it |
| Labelled `skip` | **parked** — the user wants it out of this run |

Confirm the PR is genuinely `MERGED` before counting it as merged; `CLOSED` is a park.

### 9. Next PR

`git fetch origin development` so the next PR merges against the branch this one just landed on, then start again at step 1.

## Final report

When the queue is empty, print a table of every PR with its outcome, and the reason for each park.
