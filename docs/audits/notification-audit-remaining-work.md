# Notification audit — remaining work

Single tracker for audit items that are **out of scope** or **not yet closed**
across the notification-audit PRs. Delete a row when its work lands. Two PRs
feed this: the QA-followup batch (#185, merging first) and the audit
consolidation (#184, rebased after #185).

## From #185 — QA-followup batch (merging first)

### Round-2 deferrals
- **N022 / BUL-192 — SMS "Identity not provided."** Twilio sender-identity /
  A2P clean-number provisioning. Infrastructure, not a code fix. The bid_url and
  deadline tokens are already correct on this branch (re-QA).
- **N044 / BUL-267 — link destinations.** The upsell links point at the
  coming-soon list. Tom wants the specific service / active list, but the active
  page lists the member's own subscriptions and would not show a service they
  have not joined. Needs a product call (active vs available vs specific-service
  deep link) before changing `url-tokens.ts`.
- **N044 vs N045 + 6am over-firing — BUL-267.** N044 fires immediately on
  stage-change (no cron). Tom reports ~8 notifications across N042/N044/N045 the
  day after signup and a 6am fire, and that N044/N045 "seem the same" (N045 is
  BUL-229). Needs a dedup / scheduling decision spanning all three; own ticket.

### Items flagged on the round-1 tickets (verify or split)
- **N004 / BUL-227 — email content still hardcoded.** Per Gustin's note, the
  dashboard channel was tokenized but the email channel still uses hardcoded
  template content; `{{message_body}}` renders in both the details box and the
  email body ("Wrong Format"). The dashboard/SMS/inbox channels are detached.
- **N056 / BUL-251 — email omits the next service date**, and the trigger
  granularity ("service category vs service") is an open product question. The
  star rating and cost formatting are addressed on this branch.
- **N006 / N008 (BUL-226 / BUL-225) — "not triggering."** `conversation.message_received`
  was reported not firing on staging. This branch adds a Twilio Conversations
  webhook diagnostic; the root cause matches the staging-webhook /
  Vercel-protection gap. Re-QA against the redeployed worker before treating as
  a code defect. The `{{thread_url}}` deep-link to the specific message (vs the
  general inbox) should also be confirmed.

## From #184 — audit consolidation (rebase after #185)

`docs/audits/pr-184-followups-and-merge.md` on the `integration/notif-audit-merge`
branch carries the detail and a channel-detach detector; fold it in here when
#184 lands and delete that copy. Outstanding ticket work found in the 34-ticket
audit:

| Ticket (code) | Effort | What remains |
|---|---|---|
| BUL-239 (N029) | partial | Dashboard settings link restored (migration 0408). Remaining: the welcome email's service-icons block (no icons + blue gap) is a Sanity icon-resolution + renderer issue needing functional QA — see `packages/notification-emails/src/blocks/services/email.tsx` + `block-data-builder.ts` `parseServices`. |
| BUL-250 (N049) | ✅ done | Vendor copy of `service.pricing_changed` now routes its message center to the vendor inbox via a target-aware URL branch. |
| BUL-260 (N095) | ✅ done | The `schedule_export` loader now resolves `schedule_date` from the earliest planned date of the shared jobs. |
| BUL-215 (N075) | ✅ done | The review follow-up's message center resolves to the vendor ratings page (URL branch), and the email button's hardcoded link is retokenized (migration 0407). |
| BUL-255 (N082) | ✅ done | The admin payment-completed copy now fires on the actual charge at job completion (credit-covered + confirmed card), not at checkout. Tokens were already fixed in 0406. Note: recurring services now send one copy per completed charge (the intended completion trigger). Emit-on-success integration-test coverage is a follow-up (no Stripe mock in the payment test). |
| BUL-289 (N065) + BUL-265 (N106) | partial | The digest "Complete" buttons now open the vendor **jobs page** (`/dashboard/vendor/my-jobs`) instead of the read-only calendar — the core "button doesn't work / wrong page" complaint. Remaining (enhancement, needs route work + product call): per-job deep-linking. The block carries no job id and the my-jobs page has no `?job=` anchor; plumbing a job id through `getVendorJobs`/`BlockData.jobs` + adding my-jobs deep-link support would land the vendor on the specific job. An email button that *mutates* (marks complete) is out — deep-link is the safe interpretation. |
| BUL-244 (N018) | QA | Round-2: reaching all block members, not only interested. On-branch query looks correct; needs functional QA. |
| BUL-314 (N050) | — | Code fix appears present and works; ticket still Todo. Status hygiene only. |

### Rebasing #184 after #185 merges
Both PRs touch the same generated files and migration numbers. Once #185 is in
`development`:

1. **Migrations.** #185 occupies `0391_detail_vendor_bid_decline_alert`,
   `0392_detach_n051_sms`, `0393_detach_n044_inbox`. Renumber #184's `0391`–`0403`
   to follow (→ `0394`–`0406`): rename each `.sql`, re-chain every
   `meta/0XXX_snapshot.json` via `prevId`, rebuild `meta/_journal.json`. All are
   data-only, so `pnpm drizzle generate` must report "No schema changes."
2. **Snapshot.** `docs/plans/now/notifications-config-snapshot.json`: union the
   channel flips (the two PRs change mostly different codes; both ended up
   flipping N001/N060/N123 — keep one).
3. **Registry.** Do not hand-merge `entries.generated.ts`; resolve the snapshot,
   then regenerate. Pick one quote convention repo-wide (#184 commits raw
   double-quote generator output; #185 ran Prettier to single quotes).
4. **`channel-policy.ts`.** Both add N001/N060/N123 to `GRANDFATHERED_SMS_GAPS`;
   keep the `// detached`-annotated version.
5. Re-run `cd packages/notifications && pnpm test` and `pnpm drizzle generate`.
