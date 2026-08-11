# Notification content merge: development -> main (APPLIED 2026-08-07)

Applied to main on 2026-08-07 after review. All 8 statements reported `UPDATE 1` and committed in
a single transaction. Row counts are unchanged (146 templates / 147 emails / 136 alerts / 1
cancellation), bookkeeping columns are untouched on all 146 templates, and `history` still holds
758 rows, so nothing was written through the admin UI path.

Two deliberate deviations from development remain, both decided at review:

- **N040 alert message** is trimmed of the trailing space development carries.
- **N056 email body** keeps main's 7-block structure with development's `{{service_name}}` token.
  Development's two empty artifact blocks were not copied.

A re-comparison against development after applying shows those two fields and nothing else.

SQL: [`notification-sync-2026-08-07.sql`](notification-sync-2026-08-07.sql),
8 statements in one transaction. Pre-change dump of main:
[`notification-sync-2026-08-07-main-backup.sql`](notification-sync-2026-08-07-main-backup.sql)
(templates, emails, dashboard alerts, cancellations).

- Source: Neon branch `development` (`<branch-id>`, host `ep-morning-pond-a6zdcsmk`)
- Target: Neon branch `main` (`<branch-id>`, host `ep-noisy-mud-a6nccr2r`) -- production

Unlike the 2026-08-03 run, both branches took edits since the last sync, so this is a
reconciliation rather than a copy. One template needed a hand-merge.

## Scope

Both branches hold 146 templates, 147 email rows, 136 alert rows, 0 inbox messages, 1
cancellation. Column definitions on all six notification tables are byte-identical, so nothing
here is structural. Code sets match, `code -> id / email_id / dashboard_alert_id / inbox_message_id`
linkages match, and the cancellation row matches. No inserts or deletes are proposed.

Five templates differ, across 8 content rows:

| Code | Label | Differing fields |
| --- | --- | --- |
| N019 | New Service Starting Soon (AM TRIGGER) | email content (#123) |
| N040 | Upcoming service reminder (AM TRIGGER) | sms (#107), email content (#74), alert message (#61) |
| N056 | Post-Job confirmation, invoice + review | email content (#136) |
| N059 | One-time proposal accepted + booked | email content (#86) |
| N154 | Vendor On the Way | sms (#148), email content (#199) |

All five are `active` in the registry. N019, N040, N056 and N059 have `sms: true` and
`email: true`; N154 has `sms: true` and `email: false`, so its email body is inert and is synced
only for parity. The SMS changes on N040 and N154 will send.

Structural columns (`event_name`, `target`, `status`, `subject_type`, `conditions`,
`recipient_strategy`, `recipient_params`, `category`, `scheduling_*`, `repeat_policy`) are
identical on all five, so this does not fight the registry materialization.

## Both branches diverged from a shared ancestor

Edit history is common up to 2026-06-24 21:14, then splits:

| Branch | Edits since the split |
| --- | --- |
| development | 2026-07-22 (admin 3), 2026-07-24 (admin 3), 2026-08-04 (admin 5), 2026-08-06 (admin 3) |
| main | 2026-07-27 (admin 5), 2026-08-06 (admin 3) |

main therefore carries production-side work that development never received by merge. The
important finding is that it was never *lost*: for N019, N040, N059 and N154, every block and
field other than the ones listed below is byte-identical across the two branches. main's
2026-07-27 edits are already present in development, which reads as the same admins making the
same edit in both places. Nothing on main is overwritten by this draft except the fields named
here.

The 2026-08-03 sync wrote no `notification_template_history` rows because it ran as direct SQL,
so history counts (972 dev / 758 main) understate what has been applied. Content was compared
directly, per AGENTS.md.

## Group A: one editorial campaign, dev-forward, no conflict

N019, N040, N059 and N154 are a single pass by admin 5 on 2026-08-04 adding pet-safety language
to the pre-service notifications. Each is additive against main.

| Code | main (current) | development (proposed) |
| --- | --- | --- |
| N019 email list item | Make sure gate/access is available on {{service_date_first}} | Make sure access is available and pets are secured on {{service_date_first}} |
| N040 sms | ...scheduled for {{service_date}} with {{vendor_name}}. View: ... | ...with {{vendor_name}}. **Please ensure access is available and pets are safely secured.** View: ... |
| N040 email list item | Ensure gate/access is available | Ensure gate/access is available and pets are safely secured |
| N040 alert message | Reminder: {{service_name}} is coming up on {{service_date}}. | Reminder: ... **Please ensure access is available and pets are safely secured.** |
| N059 email list item | Make sure access is available on {{service_date}} | Make sure access is available and pets are secured on {{service_date}} |
| N154 sms | ...is on the way for your {{service_category_name}} service. See you soon! | ...service. **If you have pets, now's a good time to bring them inside.** See you soon! |
| N154 email paragraph | same sentence, without the pets line | same addition as the SMS |

N019 and N059 drop "gate/" from the phrasing; N040's email list item keeps it. N154's block also
gains `paragraph_size: "default"`, a defaulted parameter with no rendered effect.

## Group B: N056 is the real conflict and is hand-merged

Both branches edited N056 on 2026-08-06, roughly an hour apart, by the same admin:

| Time (2026-08-06) | Branch | Edit |
| --- | --- | --- |
| 19:32:50 | development | email content |
| 19:33:21 | main | dashboard alert |
| 19:33:45 | development | dashboard alert |
| 19:34:32 | main | email content |
| 20:34:33 | development | email content |

The dashboard alert converged: it is identical on both branches now. Only the email body differs,
and development holds the later edit. Decomposed, development's body is main's body plus three
changes:

1. **A token change, worth taking.** The "Service:" line in the summary list reads
   `{{service_category_name}}` on main and `{{service_name}}` on development. N056's subject type
   is `service_job`, and that loader selects both columns
   (`packages/notifications/src/delivery/template-resolution.ts:942`), so both render. The change
   names the specific service rather than its category, which matches the direction of the
   recent one-off-service work. The later "Your next {{service_category_name}} is scheduled"
   sentence is unchanged on both sides.
2. **An empty text block** inserted between the what-happens-next container and the footer.
3. **An empty cards block** appended after the footer.

Blocks 2 and 3 have no content: an empty heading, sub-header and paragraph, and an empty card
list. They read as admin-UI artifacts from an interrupted edit, not as copy, and appending a
block after the footer is not a layout anyone chose. Copying development's body wholesale would
ship both into the production invoice email.

**The draft therefore takes main's 7-block structure with only the token swapped.** The merged
payload was verified block by block against main: it differs in exactly one block, and that block
differs in exactly the one token. This is the only statement in the set that is not a
straight copy from development.

## Review flags

1. **N056's token direction was confirmed dev-forward at review.** Development's 20:34 edit is the
   intended final state; main's 19:34 edit was superseded. The empty artifact blocks were still
   dropped, so main now holds the token change without them.
2. **N040's alert message was trimmed of its trailing space** at review, rather than copied
   verbatim.
3. **Development still holds both deviations.** Its N040 alert message keeps the trailing space
   and its N056 body keeps the two empty blocks, so the next comparison run will surface them
   again. Clearing them in development would make the two branches converge exactly.
4. **Bookkeeping columns are untouched by design.** No `updated_at` bump and no history rows, so
   after this lands main's timestamps still will not reflect it. Compare content, not timestamps.
5. **Registry labels still diverge.** The registry calls N019 "New Service Starting Soon" and
   N040 "Upcoming service reminder (recurring + one off)", while both databases carry the
   "(AM TRIGGER)" suffix. Not touched here, but a future registry materialization could revert
   the database labels.

## Safety properties of the SQL

- Every statement targets a content row by `id`, never by template `code`. None of the six target
  ids (emails 74/86/123/136/199, alert 61, templates 107/148) is shared with another template;
  verified read-only against main. The known shared rows (email 67 for N107/N115, alert 109 for
  N109/N121) are outside this change set.
- Only the 8 differing fields are set. `created_at`, `updated_at`, `created_by_admin_id` and
  `updated_by_admin_id` are untouched, so main keeps its own history.
- jsonb payloads are dollar-quoted and cast explicitly; all five parse as JSON.
- Wrapped in `BEGIN`/`COMMIT`, run with `ON_ERROR_STOP=1`.
- Dry-run against main with `COMMIT` swapped for `ROLLBACK`: all 8 statements reported
  `UPDATE 1`, then rolled back. Both branches were re-snapshotted immediately before applying to
  confirm the change set had not moved.

## Comparison method, and one trap in it

Content was compared two ways, which agree: a per-template JSON snapshot joined across all four
content tables, and a per-row hash of the email and alert tables by `id` (which also covers the
two email rows, 131 and 180, that no template references; both are identical).

The first hash attempt used `md5(col || col || ...)`. One alert column is NULL on row 61, so the
whole concatenation went NULL, the row printed as a blank line on both branches, and the diff
came back clean while the row genuinely differed. `concat_ws` fixed it. Any future run of this
comparison should be NULL-safe, or it will under-report.
