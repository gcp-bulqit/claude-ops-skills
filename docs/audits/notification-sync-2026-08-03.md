# Notification content sync: development -> main (APPLIED 2026-08-03)

Applied to main on 2026-08-03 after review. All 7 statements reported `UPDATE 1` and committed in
a single transaction. The full 146-template comparison shows zero differences afterward, and row
counts are unchanged (146 / 147 / 136 / 1). Pre-change dump:
[`notification-sync-2026-08-03-main-backup.sql`](notification-sync-2026-08-03-main-backup.sql).

main's bookkeeping columns are untouched, as intended: N074 and N075 still read
`updated_at=2026-07-27 17:37` (admin 5) and N086 `2026-06-19 16:01` (admin 3), and no
`notification_template_history` rows were written. Timestamps on main therefore do not reflect
this sync, which is the drift trap AGENTS.md describes: compare content, not timestamps.

- Source: Neon branch `development` (`<branch-id>`, host `ep-morning-pond-a6zdcsmk`)
- Target: Neon branch `main` (`<branch-id>`, host `ep-noisy-mud-a6nccr2r`) -- production
- SQL: [`notification-sync-2026-08-03.sql`](notification-sync-2026-08-03.sql), 7 statements in one transaction

## Scope

Both branches hold 146 templates, 147 email rows, 136 dashboard-alert rows, 1 cancellation.
Code sets are identical, `code -> id/email_id/dashboard_alert_id` linkages are identical, and
the cancellation row matches. No inserts or deletes are proposed.

Three templates differ, across 13 columns:

| Code | Template row | Email row | Alert row |
| --- | --- | --- | --- |
| N074 Member review (vendor) | label, description, sms | subject, content (#145) | title, message (#82) |
| N075 Member review + follow-up (vendor) | label, description, sms | content (#146) | message (#83) |
| N086 Payment failed (member) | -- | content (#148) | -- |

Structural columns (`event_name`, `target`, `status`, `subject_type`, `conditions`,
`recipient_strategy`, `recipient_params`, `category`, `scheduling_*`, `repeat_policy`) are
byte-identical on all three. This is a pure copy sync, so it does not fight the registry
materialization.

## Direction is dev-forward, unlike the 2026-07-24 run

Edit history confirms development holds the later work on all three:

| Code | main last admin edit | development last admin edit |
| --- | --- | --- |
| N074 | 2026-07-27 17:37 (admin 5) | 2026-08-01 19:41 (admin 3) |
| N075 | 2026-07-27 17:37 (admin 5) | 2026-07-28 21:21 (admin 5) |
| N086 | 2026-06-19 16:01 (admin 3) | 2026-07-29 16:41 (admin 3) |

main carries one production-side edit development never received: the 2026-07-27 17:37 email
edits on N074 and N075 by admin 5. Development was edited again the next day by the same admin,
which reads as the same person reworking the copy properly in dev. Confirm that reading before
applying, because those production edits are what this overwrites.

## What changes on production

### N074 -- reframed from "negative review" to neutral "review"

| Field | main (current) | development (proposed) |
| --- | --- | --- |
| label | Member negative review | Member review |
| description | A negative review is submitted; vendor notification. | A review is submitted; vendor notification. |
| sms | Bulqit : A negative review was submitted at {{property_address}} by {{first_name}} {{last_name}}. Please respond within 24-48 hours. | Bulqit : A review was submitted at {{property_address}} by {{first_name}} {{last_name}}. Please review and respond if necessary within 48 hours. |
| email subject | You received a review that needs attention | You received a review |
| alert title | Negative Rating Alert - {{stars_rating}} star review ... | Rating Alert - {{stars_rating}} star review ... |
| alert message | A negative review was submitted. Please respond within 24-48 hours. | A review was submitted - please review and respond if necessary within 48 hours. |

Email body blocks also change: label text `LOW REVIEW ALERT` -> `REVIEW ALERT`, label variant
`alert` -> `primary`, the paragraph "A low rating was submitted" drops "low", and the two-step
instruction list becomes conditional ("If the review is good, you're done").

### N075 -- same reframing, plus new SMS copy

| Field | main (current) | development (proposed) |
| --- | --- | --- |
| label | Member negative review + requests follow up | Member review + requests follow up |
| description | Negative review requesting follow-up; vendor notification. | Review requesting follow-up; vendor notification. |
| sms | NULL | Bulqit : A review was submitted at {{property_address}}, and {{first_name}} requested a follow-up. Please review and respond within 48 hours. |
| alert message | A negative review requested follow-up. | You received a review, and they requested a follow-up. Please view and respond within 48 hours. |

Email body: same "low rating" and instruction-list changes as N074. Subject and alert title are
unchanged.

### N086 -- removes a support paragraph from production

This one is a deletion, so read it carefully. main's payment-failed email ends with a block
development does not have:

> If you believe this is an error or need help, reply to this notification or contact our
> [Support Team]({{message_center_url}}).

That block was present in both after the 2026-07-24 sync; development's 2026-07-29 edit removed
it. Applying this draft removes it from production too. If the removal was accidental in dev, fix
dev first and re-run the comparison rather than syncing.

## Review flags

1. **N086 is a content removal on a billing email.** See above. The only change to N086.
2. **The SMS copy is inert either way.** The registry sets `sms: false` for both N074 and N075
   (`packages/notifications/src/registry/entries.generated.ts`), and the code registry gates
   channels, not the database column. N075's new SMS text will not send until the registry
   changes.
3. **Labels diverge from the registry.** The registry still calls N074 "Member negative review
   anonymous" and N075 "Member negative review + requests follow up". This sync moves the database
   further from those values, so a future registry materialization migration could revert the
   labels. Worth landing the rename in the registry as well.
4. **N074's address token moves.** dev renders `Address: {{property_address}}` as the label with
   `{{street_address}}` beneath; main uses `Address:` with `{{property_address}}` beneath. Both
   tokens are declared for the review subject, but `loadReviewSubjectRow`
   (`packages/notifications/src/delivery/template-resolution.ts:473`) selects neither address
   column, so both variants likely render empty today. Pre-existing, not caused by this sync.

## Safety properties of the SQL

- Every statement targets a content row by `id`, never by template `code`. None of the six target
  ids (emails 145/146/148, alerts 82/83, templates 87/88) is shared with another template. The two
  shared rows that do exist in both databases (email 67 for N107/N115, alert 109 for N109/N121) are
  outside this change set.
- Only the 13 differing columns are set. Bookkeeping columns (`created_at`, `updated_at`,
  `created_by_admin_id`, `updated_by_admin_id`) are untouched, so main keeps its own history.
- Values were generated from development with `quote_nullable`, and jsonb payloads are cast
  explicitly. All three payloads were verified to parse as JSON (4, 6, and 4 blocks).
- Each target id was verified read-only on main to match exactly one row.
- Wrapped in `BEGIN`/`COMMIT`; run with `ON_ERROR_STOP=1` so any failure aborts the whole set.

Not yet done, and required before applying: a full pre-change dump of the three tables from main.
The 2026-07-24 backups were written to the session scratchpad and no longer exist, so this one
should go somewhere durable.
