---
name: slot-promotion
description: "Analyze and describe a blue/green promotion PR merging development into a deploy slot. Derives the target slot from the PR's base branch, splits the carried PRs by whether the other slot has already served them, classifies the migrations and the hazards they carry, runs read-only pre-flight checks against the production database, drafts the promotion description, and sweeps the delivered Linear issues. Use when a deployment PR into blue or green needs its description, review, or pre-merge checks. Examples: \"write the description for pull/1040\", \"prepare the promotion into green\", \"review the deployment PR\", \"what is left before I can merge the promotion\""
---

# Describing a promotion into a deploy slot

Input is a **PR number**, nothing else. The target slot comes from that PR's base
branch, so blue and green can never be transposed by a typo. Everything else is
derived.

Read [`AGENTS.md`](../../../AGENTS.md) for the slot rules this skill enforces.
The two facts that drive most of the analysis:

- **The slots share one production database.** A migration that rides a promotion
  into one slot is already live for the other, including the one currently serving.
- **The second slot promoted usually finds its migrations already applied.** Check
  the high-water mark before describing a migration step that has no work to do.

## Refuse rather than proceed

Stop and say why if any of these hold:

| Condition | Why |
| --- | --- |
| base is not `blue` or `green` | not a promotion |
| head is not `development` | a slot takes code only as a promotion of development |
| base is the slot **currently serving traffic** | promoting into it changes what production is serving right now |

`slot_state.py` returns `REFUSE` for the third. Serving is read from the newest
production deployment's alias list, never from a branch name.

## Procedure

Work in the session scratchpad. Only backups, audit docs and the checkpoint
belong in the repo.

### 1. Derive the state

```bash
python3 .claude/skills/slot-promotion/scripts/slot_state.py <PR> > $S/state.json
```

Gives target slot, other slot, both carried groups, tip shas, the merge-tree
check, the serving slot, and the environment of the last production worker deploy.

Two of those are worth understanding rather than trusting blindly:

- **The carried split.** `target..other` is what the other slot already serves;
  `other..development` is new to both. Their sum must equal the PR's commit count.
  If it does not, something is wrong with the assumption that both slots descend
  from development, and the discrepancy is the story, not a rounding error.
- **The last worker deploy environment.** `head_branch` reads `development` on
  every `workflow_dispatch` regardless of what was deployed. Only the run log's
  `ENVIRONMENT:` line says which slot reached the unsuffixed production Fly apps.

### 2. Scan the migrations

```bash
python3 .claude/skills/slot-promotion/scripts/migration_scan.py --target <slot> [--hwm <n>]
```

Classifies every migration new to the target and flags the hazards below. Pass
`--hwm` once you have step 3's number so it can tell you which migrations
actually run.

If the target's journal max already covers every new migration, the promotion has
**no migration step**. Say so plainly and explain why (the other slot's build
already committed them against the shared database), rather than listing files
that will not run.

### 3. Pre-flight against production, read-only

Only when the batch carries migrations. Resolve the URI through the Neon API;
never hardcode it.

```bash
NEON_KEY=$(grep '^NEON_KEY=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
URI=$(curl -s -H "Authorization: Bearer $NEON_KEY" \
  "https://console.neon.tech/api/v2/projects/<neon-project-id>/connection_uri?branch_id=<branch-id>&database_name=neondb&role_name=neondb_owner" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['uri'])")
env -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER psql "$URI" -Atq -c "<SELECT>"
```

- `<branch-id>` (host `ep-noisy-mud-a6nccr2r`) is **production**.
  Confirm the host before querying. **Never** connect to `<branch-id>`
  (staging); it is read-only per AGENTS.md and not the target here anyway.
- `env -u PG*` is not optional. A devenv shell exports `PGHOST`/`PGPORT` and they
  silently override the URI, sending the query to localhost.
- **SELECT only.** Never run the migration against production to see what it
  would do, not even inside a transaction you intend to roll back.

Always read the high-water mark, because every migration claim depends on it:

```sql
SELECT max(created_at) FROM drizzle.__drizzle_migrations;
```

### 4. Dry-run any content migration

```bash
python3 .claude/skills/slot-promotion/scripts/dryrun_sql.py --tag <tag> > $S/dryrun.sql
env -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER psql "$URI" -f $S/dryrun.sql
```

Rewrites each `UPDATE` as a `count(*)` over its own guard. Two results matter:

- **`rows_matched = 0`** means production copy has drifted from the pattern and
  that statement is a no-op there. The fix does not land on that surface. Report
  it; do not describe the migration as if it applies everywhere.
- **`scope = UNSCOPED`** means a statement carries no template-code filter and
  rewrites every matching row in the table. Identify which rows those are before
  merging, because the migration's own header will name a smaller set.

### 5. Capture the pre-image

Any migration that rewrites rows gets a restorable backup first, into
`docs/audits/` (never the scratchpad, which is deleted):

```
docs/audits/notification-<tag>-preimage-<DATE>.sql
```

Emit `UPDATE ... WHERE id = ...` statements built with `quote_literal`, wrapped in
`BEGIN`/`COMMIT`, with the branch and host in a header comment. Also confirm no
other template shares a row being rewritten: `notification_templates.email_id`
is **not** unique, so one content row can back several templates.

**Build the backup from `content_targets` in the scan output, never from a guess
about which tables hold copy.** Notification copy lives in three places, and they
do not share a naming pattern:

| table | column |
| --- | --- |
| `notification_template_emails` | `content` (jsonb) |
| `notification_template_dashboard_alerts` | `message` |
| `notification_templates` | `sms` |

The third is the trap. A pattern like `notification_template_*` matches the first
two and silently misses `notification_templates`, whose `sms` column `0543`
rewrote on N039 and N057. A backup built that way looks complete, covers ten of
twelve statements, and cannot restore the SMS copy. Assert that every table in
`content_targets` appears in the generated file before calling the capture done.

### 6. Draft the description

Structure, in order. Delete a section only when it genuinely does not apply.

1. `## Promotion: development -> <slot>`. Tip sha, that head is development so the
   tree is identical by construction, which slot is serving and since when, how far
   behind the target is, that the target carries only merge points, and merge-commit
   instruction.
2. `### Carried since #<last promotion> (N PRs)` with `#### Already serving on
   <other> since #<PR>` and `#### New to both slots`. State plainly that review
   attention belongs on the second list.
3. `### Database migrations`, or that there are none, and why.
4. A section per hazard (below). One heading each, named for the consequence.
5. `### Environment configuration`
6. `### Worker impact`
7. `### Ops actions that do not ride in the diff`, from the checkpoint, step 8.
8. `### Housekeeping`
9. `### Verification`. Every load-bearing claim, each as an observed fact.
10. `### Merge instructions`, numbered, with the pre-flight results folded in.

Title: `Merge Development into <Blue|Green> - MM/DD/YYYY`.

### 7. Push on approval

Show the draft and **wait for an explicit go**. Then:

```bash
gh pr edit <PR> --title "<title>" --body-file $S/body.md
```

Verify afterwards that the base, head and section list are what you intended.

### 8. Update the ops checkpoint

`docs/audits/checkpoints/slot-promotion-ops.json` holds what no diff can tell you:
work a PR shipped that stays inert until someone configures something. Read it,
carry every `open` item into the description, and ask whether any has been done
since the last promotion. When one is closed, set `status`, `closed_at` and
`closed_in_promotion` rather than deleting it, so a later promotion can say how
long it was carried. Add an item whenever this batch ships something inert.

### 9. Sweep the Linear issues

```bash
python3 .claude/skills/slot-promotion/scripts/linear_refs.py <new-to-both PR numbers>
```

**Only the new-to-both PRs.** The other group was swept by the previous promotion.

- **Delivered** = tickets in the PR title's brackets. This is the sweep set.
- **Body-only** = usually the opposite: deferred, tracked separately, frozen in a
  backlog. Read the printed context for each and exclude it unless the body says
  the PR closes it. Sweeping these marks unshipped work live.

Show the table with each issue's current status, get a go, then set the delivered
set to `Live - Final Testing`. Flag anything moving **backwards** (a `Done` issue)
or forwards from `Backlog`, and flag any issue whose Linear record carries a PR
that has not merged, which means the status will run ahead of the code.

## Hazards worth their own section

These are the three that have actually happened. `migration_scan.py` detects them;
the description has to explain the consequence.

**A backfill meeting a unique index.** A partial unique index over a column that
was created NULL cannot conflict, because NULLs are distinct. The migration that
fills the column is the one that can violate it, and it will abort mid-deploy with
earlier migrations already committed. Write a pre-flight that applies the
migration's own attribution rule to the rows the index predicate covers and expects
no group above one, run it, and record the result. Precedent: `0542` against
`0539`'s `service_jobs_one_time_live_uq`.

**Notification copy landing before the code that fills it.** A migration rewrites
template content to use a token whose loader ships in the same batch. The renderer
degrades an unresolvable token to an empty string, so nothing errors and nothing
alerts; the copy just loses a clause. The window opens when the migration commits
and closes when the worker is redeployed from the promoted slot, which is **after**
cutover. Name the affected notices, say which fire on real production events, and
say whether the failure truncates a sentence or drops a self-contained one.
Precedent: `0540`/N063, `0543`/`next_visit_note`.

**A backfill of a column the serving slot already reads.** Check before calling
anything inert: `git grep -n '<table>\.<column>' origin/<serving-slot> -- apps packages`.
If the serving slot reads it, the migration changes live behaviour at build time,
not at cutover. Say which direction, and quantify how many rows move. Precedent:
`0542` changing which purchase `triggerServiceJobPayment` resolves.

Two more worth checking every time:

- **The standby is not a rollback path.** If the database has moved ahead of the
  standby's built code, reverting traffic to it reintroduces whatever that gap
  causes. Say so when it applies.
- **Deploy ordering.** Never deploy workers at merge time. The worker apps are
  unsuffixed and shared by both slots, so deploying from the standby puts its
  worker code behind the serving slot's web code. Workers go out from the promoted
  slot only after traffic is cut over.

## Writing rules

- **No em-dashes.** Periods, semicolons, or rewrite. Repo-wide style.
- **Verification is observed facts only.** Every bullet is something a command
  returned: a tree hash, a count, a log line, a query result. If it was reasoned
  from source rather than executed, say that in the prose and keep it out of
  Verification.
- **Quantify.** "11 of 65 rows" beats "some rows". "7 to 12 minutes" beats "a short
  window".
- **Frame by consequence.** Section headings name what happens, not what changed:
  "Notification copy lands before the code that fills it", not "0543 details".
- **Do not pad.** A promotion with no migrations and no hazards gets a short
  description. #1031 needed no risk section and saying so was the finding.
