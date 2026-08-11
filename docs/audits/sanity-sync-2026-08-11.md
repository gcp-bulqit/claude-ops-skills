# Sanity content sync, 2026-08-11

Development to production, project `5z88vhq0`. Four documents written in one
transaction (`jf0yDYWTBDQmqH8S1OM84F`). Verify passed all five checks and the
checkpoint at `docs/audits/checkpoints/sanity-content.json` was rewritten.

Production backup taken before the write:
[`sanity-sync-2026-08-11-production-backup.json`](sanity-sync-2026-08-11-production-backup.json).
Every backed-up document carried revision `KmkMw2CM7j4yWChl7F4ZBb`.

## Direction was unambiguous

Production had not moved a single field since the 2026-08-10 checkpoint. All four
documents still carried one shared revision from a bulk write at 18:26Z, which
precedes the 19:08Z checkpoint capture. Every difference originated in
development, so this was a fast-forward.

## What changed

**Member Platform Terms** (`/member-terms`). No copy change at all. The rendered
text was already byte-identical to production's. Development had removed 76 empty
spacer blocks and regenerated every block key, taking the body from 152 blocks to
76. The visible effect is tighter vertical spacing.

**Vendor Platform Terms** (`/vendor-terms`) and **Member and Vendor Service
Agreement** (`/bulqit-service-agreement`). Both pages were serving roughly 5.7k
characters of pre-launch boilerplate that described the site as informational and
promised a launch in early 2026. Both now carry drafted, distinct legal documents
dated August 10, 2026, at 56k and 27k characters.

**refer**. The `description` field changed from a plain string to portable text so
that "Environmental Defense Fund" renders as a link to `edf.org/about`. This is the
content half of BUL-1403. The `form.initialTitle` field, which held a single space,
was dropped.

**Subheadline removed from all three legal pages.** Production carried
`subheadline: "Terms of Service"`; development had no such field. The field is
still defined in the schema and still rendered when present, so this was a real
visible change rather than a schema retirement. It was approved deliberately.

## The tooling reported 1896 conflicts that did not exist

The first comparison put 1896 fields in CONFLICT and forced Path B. Every one was
false, and they shared a single shape: a field created in development after the
checkpoint, absent in production, and absent from the baseline entirely.

`compare.py` looked the baseline hash up with a bare `.get(k)`, which returns
`None` for a key the checkpoint never recorded. It then compared that `None`
against `h('<absent>')`, which is a hash string and never equal, so production
scored as moved on every field development had newly created. The fix is to
default both lookups to the `<absent>` sentinel, since a key missing from the
checkpoint means the field existed on neither side at the time.

`verify.py` already documented this hazard where it writes the checkpoint: it
deliberately hashes over the union of keys with that same sentinel, noting that
storing only present keys makes a key absent on one side look like that side
moved. The checkpoint writer was correct; only the reader was wrong.

After the fix: 3495 pushes, zero conflicts, zero preserves, and the 76 accepted
divergences stayed settled.

## Two further traps, both now fixed in the tooling

**Piping the comparison through `head` silently discarded the plan.** `compare.py`
printed its full report before writing `plan.json`. Truncating stdout killed it
with SIGPIPE first, so the plan on disk stayed whatever the previous run had left
there, and a later step would build mutations from a stale plan while the summary
on screen described the new one. This was caught only because the refer keys in
the plan disagreed with the printed summary. The plan is now written before the
report, and the skill says to redirect rather than pipe.

**The mutation builder could not express a deletion.** It emitted only `set`, so a
field present in production and absent in development had no common ancestor to
patch and was refused as "no common ancestor", which reads as a structural problem
rather than the ordinary field removal it was. The three subheadline removals were
applied by hand as an `unset` on the existing per-document patch. The builder now
emits `unset` itself and drops any unset nested inside a subtree already written
whole. Replayed against this run's snapshots it reproduces the three legal
documents byte for byte and replaces the whole-object `set form` on `refer` with a
surgical `unset form.initialTitle`, which is the same net result against less
surface.

A regression test over both fixes lives in the skill's history rather than in CI:
comparing this run's snapshots against a synthetic checkpoint seeded so that
development and production start equal yields Path A with zero conflicts under the
corrected logic and Path B with 1914 conflicts under the old.

## The refer change is coupled to deployed code

`refer.description` is typed `string` on `main` and rendered directly into a
paragraph, so an array there would crash the page. It is typed as an array on
`development`, `blue`, and `green`, all of which branch on `Array.isArray` and
handle both shapes.

www.bulqit.com is currently served by the green slot, so the page renders the new
portable text correctly, confirmed live: the link element is present and points at
`edf.org/about`. The dependency is worth remembering. A deployment built from
`main` would break this page for as long as it served traffic.

## Verification

All five verify checks passed, and the live pages were checked directly. The two
rewritten agreements carry the August 10, 2026 date and no longer contain the
pre-launch boilerplate. Neither `/vendor-terms` nor `/member-terms` renders a
subheadline element. The referral page renders its description as portable text
with a working outbound link.

The checkpoint is uncommitted. Without it the next run cannot tell which side
changed.
