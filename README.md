# tool-shed

Operational skills for the Bulqit platform, kept alongside the audit records that
explain why each one works the way it does.

These are Claude Code skills: a `SKILL.md` describing a procedure, plus the Python
that carries it out. Each exists because the task it covers has a way of going
wrong quietly, and the write-ups in `docs/audits/` are the record of it going wrong.

## Layout

Paths mirror the `bul-website` repository exactly, so every relative link inside a
`SKILL.md` resolves here as it does there, and any file can be copied back to the
same path.

```
.claude/skills/          the skills themselves
docs/audits/             audit write-ups, database and content backups
docs/audits/checkpoints/ per-skill sync state
```

## The skills

**`sanity-content-sync`** reconciles Sanity CMS content between the development and
production datasets. Asset ids are derived from file content and the same artwork
was uploaded separately into each dataset, so the two hold different ids for the
same image. A naive document copy rewrites working references into ones that do not
resolve; an early analysis found a wholesale copy would have produced 23 broken
images for zero visible content change.

**`neon-notification-sync`** reconciles notification template copy between Neon
branches. Template content lives in the database rather than in code, so it diverges
between environments independently of any deploy. A sync applied as direct SQL bumps
no `updated_at`, which makes timestamps useless for deciding which side changed.

**`slot-promotion`** describes and pre-flights a blue/green promotion PR. Both slots
serve from one production database, so a migration riding a promotion into one slot
is already live for the other.

**`twilio-pool-numbers`** buys and moves Twilio masking-pool numbers. Binding
uniqueness is account-wide rather than per-service, so two environments cannot share
a number.

## Direction is decided by checkpoint, never by timestamp

The two sync skills classify each field by comparing both sides against a recorded
hash from the last agreed sync:

| dev moved | prod moved | verdict |
| --- | --- | --- |
| no | no | SETTLED, an accepted divergence |
| yes | no | PUSH |
| no | yes | PRESERVE, never overwritten |
| yes | yes | CONFLICT, a human decides |

Recording both sides is what lets a deliberate divergence stay quiet instead of
being re-proposed on every run. Without the checkpoint nothing is decidable and the
run degrades to a full manual merge.

## Reading the checkpoints

`docs/audits/checkpoints/*.json` hold a hash per field for both sides as of the last
agreed sync. They are state, not documentation: the copies here are a snapshot from
when this repository was written, and the live ones travel with `bul-website`, where
the skills actually run. Do not sync against the copies in this repository.

## What is deliberately not here

Backups from the phone-removal audits that contain customer phone numbers and
personal email addresses. The Twilio numbers that do appear, in
`twilio-conversations-environment-overlap.md`, are company-owned masking-pool
numbers, kept legible because the pool skill refers to them. Vendor phone numbers in
that document are hashed.

No credentials are stored here. The scripts read tokens from the environment or from
a local `.env`.
