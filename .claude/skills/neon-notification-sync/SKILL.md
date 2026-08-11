---
name: neon-notification-sync
description: "Reconcile notification template content between the Neon development and main branches. Use when notification copy differs between environments, when an email/SMS/dashboard-alert edit made in development needs to reach production, or when both environments have been edited and must be merged. Examples: \"sync the notification templates to production\", \"push the notification copy from development to main\", \"compare notification content between environments\", \"merge the notification tables\""
---

# Syncing notification content between Neon branches

Template copy lives in the database, not in code, so it diverges between environments
independently of any deploy. This skill compares the six notification tables, classifies
every difference by which side actually changed, and writes only what was approved.

- Source: Neon branch `development` (`<branch-id>`, host `ep-morning-pond-a6zdcsmk`)
- Target: Neon branch `main` (`<branch-id>`, host `ep-noisy-mud-a6nccr2r`) -- **production**
- Never touch `staging` (`<branch-id>`). It is read-only, per AGENTS.md.

Background on why this is hard: [`docs/audits/notification-sync-2026-08-07.md`](../../../docs/audits/notification-sync-2026-08-07.md).

## The rule that matters most

**Compare content, never timestamps.** A sync applied as direct SQL bumps no `updated_at`
and writes no `notification_template_history` row. Those columns record edits made through
the admin UI, not every edit. On 2026-08-03 a sync overwrote N074's content on main and its
`updated_at` still read `2026-07-27`. An untouched timestamp is not evidence a row is
unchanged.

Timestamps appear in this skill's output for judging recency in a conflict. They are never
used to decide direction. Direction comes from the checkpoint.

## How direction is determined

`docs/audits/checkpoints/neon-notifications.json` records a hash per field **for both
sides** as of the last agreed sync. Each run compares each side against its own recorded
state:

| dev moved | prod moved | verdict | action |
| --- | --- | --- | --- |
| no | no | SETTLED | an accepted divergence, stays quiet |
| yes | no | PUSH | safe to send to production |
| no | yes | PRESERVE | production edit, never overwritten |
| yes | yes | CONFLICT | stop, show both, human decides |

Recording both sides is what makes SETTLED work. When a sync deliberately leaves the two
branches different (as on 2026-08-07, where main was trimmed of a trailing space development
still carries), the checkpoint remembers that, and it is not proposed again next run.

**Without the checkpoint every difference is UNCLASSIFIED** and the run degrades to Path B
with nothing decidable automatically. If the file is missing, say so and stop rather than
guessing direction from timestamps.

## Path A vs Path B

- **Path A (fast-forward)** -- every difference is PUSH. No production edits exist to lose.
- **Path B (merge)** -- anything else. Resolve each CONFLICT and UNCLASSIFIED entry with the
  human, keep every PRESERVE, then push only the approved set.

**Both paths stop for explicit approval.** Print the change summary, wait for a go. Never
apply because the gate passed.

## Procedure

Work in the session scratchpad; only backups and the checkpoint belong in the repo.

### 1. Resolve connection URIs

```bash
NEON_KEY=$(grep '^NEON_KEY=' .env | cut -d= -f2- | tr -d '"'"'"' ')
curl -s -H "Authorization: Bearer $NEON_KEY" \
  "https://console.neon.tech/api/v2/projects/<neon-project-id>/connection_uri?branch_id=<BRANCH_ID>&database_name=neondb&role_name=neondb_owner" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['uri'])"
```

Confirm the host matches the branch you intend before writing anything.

### 2. Snapshot both branches

```bash
python3 .claude/skills/neon-notification-sync/scripts/snapshot.py "$DEV_URI"  $S/dev.json  --label development
python3 .claude/skills/neon-notification-sync/scripts/snapshot.py "$PROD_URI" $S/prod.json --label main
```

The script strips `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER` from the environment. A devenv
shell exports them and they silently override the URI, sending the query to localhost.

### 3. Compare

```bash
python3 .claude/skills/neon-notification-sync/scripts/compare.py \
  --dev $S/dev.json --prod $S/prod.json \
  --baseline docs/audits/checkpoints/neon-notifications.json --plan $S/plan.json
```

Reports the path, the counts, and every difference. Conflicts list both sides ordered
most-recent-first with a full timestamp. Use `--full` to see untruncated values.

Stop here and give the human a summary. On Path B, resolve each conflict, then edit
`plan['push']` to hold exactly the keys to write.

### 4. Back up the target, durably

```bash
pg_dump "$PROD_URI" --data-only --no-owner --no-privileges \
  -t public.notification_templates -t public.notification_template_emails \
  -t public.notification_template_dashboard_alerts \
  -t public.notification_template_cancellations \
  > docs/audits/notification-sync-<DATE>-main-backup.sql
```

`docs/audits/`, never the scratchpad, which is deleted between sessions. This is the only
copy of production's prior content.

### 5. Dry run, then apply

```bash
python3 .claude/skills/neon-notification-sync/scripts/plan_to_sql.py \
  --plan $S/plan.json --dev $S/dev.json --out $S/dry.sql --rollback
psql "$PROD_URI" -v ON_ERROR_STOP=1 -f $S/dry.sql       # expect UPDATE 1 per statement, then ROLLBACK

python3 .claude/skills/neon-notification-sync/scripts/plan_to_sql.py \
  --plan $S/plan.json --dev $S/dev.json --out docs/audits/notification-sync-<DATE>.sql
psql "$PROD_URI" -v ON_ERROR_STOP=1 -f docs/audits/notification-sync-<DATE>.sql
```

Re-snapshot both branches immediately before applying and confirm the change set has not
moved. Someone editing through the admin UI mid-review is not hypothetical.

### 6. Verify and checkpoint

```bash
python3 .claude/skills/neon-notification-sync/scripts/snapshot.py "$PROD_URI" $S/prod-after.json --label main
python3 .claude/skills/neon-notification-sync/scripts/verify.py \
  --plan $S/plan.json --dev $S/dev.json --before $S/prod.json --after $S/prod-after.json \
  --checkpoint docs/audits/checkpoints/neon-notifications.json
```

Five checks: pushed fields match development, preserved fields untouched, nothing unplanned
changed, row counts unchanged, bookkeeping columns unchanged. The checkpoint is written only
on PASS. **Commit it** -- without it the next run cannot tell which side changed.

Write up what happened in `docs/audits/notification-sync-<DATE>.md`.

## Traps this tooling already handles, and why

**`email_id` is not unique.** One `notification_template_emails` row can back two templates
(row 67 backs N107 and N115; alert 109 backs N109 and N121). Field keys are therefore
`email:<id>:<column>`, never keyed by template code. Two templates sharing a row and
reconciled in opposite directions would overwrite each other and leave both wrong. Shared
rows are annotated in both the comparison and the generated SQL.

**NULL poisons a concatenated hash.** An early row-comparison used
`md5(col || col || ...)`. One alert column is NULL on row 61, so the whole concatenation went
NULL, the row printed blank on both branches, and the diff came back clean while the row
genuinely differed. Any SQL-side hashing must use `concat_ws`. This tooling hashes in Python
per column, which sidesteps it.

**Content rows no template references.** Email rows 131 and 180 are orphans. Keying by row id
covers them; keying by template code would not.

**Structural columns come from the registry.** `event_name`, `target`, `status`,
`subject_type`, `conditions`, `recipient_strategy`, `category`, `scheduling_*`,
`repeat_policy` are materialized by migration from the typed registry. If those differ
between environments, fix the registry materialization rather than syncing rows.

**Linkage is never auto-pushed.** `link:` keys (`email_id`, `dashboard_alert_id`,
`inbox_message_id`) are refused by the SQL generator. Re-wiring which content row a template
points at is not a content sync.

**The registry gates channels, not the database.** A template's `sms` column can hold new
copy that never sends because `channels.sms` is false in
`packages/notifications/src/registry/entries.generated.ts`. Check the registry before
reporting that an SMS change will take effect.

**Workers do not redeploy on merge.** This skill changes database content only, which the
notification worker reads at delivery time, so no Fly deploy is needed. A change to
`packages/notifications` itself does need one.
