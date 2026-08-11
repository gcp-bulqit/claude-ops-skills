# Notification content sync, 2026-08-11

Nothing was written to production. The comparison found no development change that
production lacked, and two production edits development was missing. Those two
were backported in the other direction, leaving the branches identical across all
3740 comparable fields.

Development backup taken before the write:
[`notification-backport-2026-08-11-development-backup.sql`](notification-backport-2026-08-11-development-backup.sql).
Applied statements: [`notification-backport-2026-08-11.sql`](notification-backport-2026-08-11.sql).

## Production was the newer side

The run classified as Path B with an empty push set: 0 push, 2 preserve, 0
conflict, 0 unclassified. Both differences were PRESERVE, meaning production had
moved since the last checkpoint and development had not.

**N030**, the welcome email body (`notification_template_emails` row 51), edited on
production at 15:58 that day. Production had removed a bare URL from the middle of
the opening paragraph, cutting "Invite your neighbors at https://bulqit.com/refer
to help launch services sooner" down to "Invite your neighbors to help launch
services sooner". The link-button immediately below the paragraph already points at
that same URL, so the inline copy was redundant.

**N064**, the vendor proposal-accepted SMS (`notification_templates` row 81),
edited on production the previous evening. Production had added a missing space
between two tokens. Development still read `{{member_last_name}}accepted your`,
which renders in a delivered message as "Smithaccepted your".

The registry has `sms: true` for N064, so that template's SMS channel does send and
the missing space was reaching recipients rather than sitting in dormant copy. The
registry gates channels, not the database, so this had to be checked rather than
assumed from the column being populated.

## Direction of the fix

Pushing development to production would have reintroduced both defects. The
divergence was closed by backporting production's two values into development
instead, which is the reverse of this skill's normal direction.

`plan_to_sql.py` takes its source snapshot as an argument and is otherwise
direction-agnostic, so the backport reused it with production's snapshot as the
source and the development branch as the target. Row identity was confirmed on both
branches first, because the generated statements address rows by id: N064 is row 81
on both, email row 51 is referenced only by N030 on both, and neither is one of the
shared content rows that back two templates.

The checkpoint was written from a normal-orientation verify, with development as
the source and main as the target. Running verify in the backport's own orientation
would have recorded the two sides swapped and mis-classified every field on the
next run.

## Verification

All five checks passed. Both branches were re-snapshotted immediately before the
write and neither had moved during review. A fresh comparison against the new
checkpoint reports IN SYNC with no field differing.

No Fly deploy is needed. This changed database content only, which the notification
worker reads at delivery time.

The checkpoint is uncommitted. Without it the next run cannot tell which side
changed.
