---
name: sanity-content-sync
description: "Reconcile Sanity CMS content between the development and production datasets. Use when site copy differs between environments, when marketing or legal copy edited in development needs to reach the live site, or when both datasets have been edited and must be merged. Handles asset references, which do not survive a naive document copy. Examples: \"sync the Sanity content to production\", \"push the CMS copy from development to production\", \"compare the Sanity datasets\", \"merge the Sanity databases\""
---

# Syncing Sanity content between datasets

Project `5z88vhq0`, datasets `development` and `production`. Production backs the live
marketing site at `bulqit.com`; there is no deploy step, so a write here is live content.

This skill compares the two datasets, classifies every difference by which side actually
changed, and writes only what was approved.

Background: [`docs/audits/sanity-sync-2026-08-10.md`](../../../docs/audits/sanity-sync-2026-08-10.md)
and [`docs/audits/sanity-sync-2026-08-03.md`](../../../docs/audits/sanity-sync-2026-08-03.md).

## The rule that matters most

**Never copy an asset reference into a dataset that does not hold that asset.**

Sanity asset ids are derived from file content, and the same artwork was uploaded separately
into each dataset, so the two hold **different ids for the same image**. `homes.svg` is
`image-0880a53c…` in development and `image-1d94b3e8…` in production. Copying a development
document into production rewrites a working reference into one that does not resolve. The
2026-08-03 analysis found a wholesale copy would have produced **23 broken images** across 12
documents, for zero visible content change.

The tooling classifies every reference before writing:

| verdict | meaning | action |
| --- | --- | --- |
| RESOLVES | development's id already exists in production | copy as-is |
| CHURN | production holds the same filename and dimensions under another id | keep production's id, copy nothing |
| UPLOAD | no production equivalent | upload first, then write the new id |

UPLOAD is the only case that adds a file, and it is always called out in the summary.

## How direction is determined

`docs/audits/checkpoints/sanity-content.json` records a hash per field **for both sides** as
of the last agreed sync. Each run compares each side against its own recorded state:

| dev moved | prod moved | verdict | action |
| --- | --- | --- | --- |
| no | no | SETTLED | an accepted divergence, stays quiet |
| yes | no | PUSH | safe to send to production |
| no | yes | PRESERVE | production edit, never overwritten |
| yes | yes | CONFLICT | stop, show both, human decides |

Recording both sides is what makes SETTLED work. The 2026-08-10 checkpoint was seeded with 76
accepted divergences already in it: asset churn on eight documents, production's own vendor
FAQ, the `_key`-only churn on a homeowner FAQ answer, and the serviceIcons split. None of
those is proposed again.

**Without the checkpoint every difference is UNCLASSIFIED.** Timestamps cannot substitute:
most production documents carry `_updatedAt = 2026-07-15T21:44`, a bulk import rather than 50
edits, and `_updatedAt` is per-document so it cannot say which *field* moved.

A field the checkpoint never recorded existed on **neither** side at the time, so both
lookups fall back to the `<absent>` sentinel. Anything else scores that field as "both sides
moved" and invents a conflict out of an ordinary development-only addition. On 2026-08-11
that produced 1896 false CONFLICTs across the three legal pages and forced Path B onto what
was a clean fast-forward. `verify.py` already hashes the checkpoint over the union of keys
with that sentinel for exactly this reason; the reader has to agree with the writer.

**A conflict count in the thousands is a tooling smell, not a content event.** Before
resolving anything, confirm the losing side actually moved. Two cheap independent checks:
recompute `h(value) != baseline.get(key, h('<absent>'))` per side, and look at `_rev` on the
documents in scope. Real edits made through the Studio arrive one document at a time, so a
whole change set sharing one `_rev` is a single bulk write whose timestamp you can place
against the checkpoint.

## Path A vs Path B

- **Path A (fast-forward)** -- every difference is PUSH.
- **Path B (merge)** -- anything else. Resolve each CONFLICT and UNCLASSIFIED entry, keep
  every PRESERVE, then push only the approved set.

**Both paths stop for explicit approval.**

## Procedure

Work in the session scratchpad; only backups and the checkpoint belong in the repo.

### 1. Export and flatten both datasets

```bash
python3 .claude/skills/sanity-content-sync/scripts/export.py development $S/dev.json  --keep-raw $S/dev.ndjson
python3 .claude/skills/sanity-content-sync/scripts/export.py production  $S/prod.json --keep-raw $S/prod.ndjson
```

The token is read from `SANITY_API_READ_TOKEN` in the environment, `.env.local` or `.env`.

### 2. Compare

```bash
python3 .claude/skills/sanity-content-sync/scripts/compare.py \
  --dev $S/dev.json --prod $S/prod.json \
  --baseline docs/audits/checkpoints/sanity-content.json --plan $S/plan.json > $S/compare.txt
```

**Redirect the report to a file; never pipe it through `head` or `less`.** It runs to
thousands of lines. The plan is now written before the report for this reason, but a
truncating pipe still kills the run with SIGPIPE partway through printing, and a report you
only half-read is how a document in scope gets missed.

Read the summary from the top of `compare.txt`, then bucket the differences by document
before reading any field paths. Four documents and 3495 fields is a normal shape; the field
list on its own tells you nothing a human can approve.

```bash
python3 -c "
import json,collections
p=json.load(open('$S/plan.json'))
for b in ('push','preserve','conflict','unclassified','settled'):
    print(b, len(p[b]), collections.Counter(k.split('|')[0] for k in p[b]).most_common())"
```

Conflicts list both sides ordered most-recent-first with the document's `_updatedAt`. The
new-files callout appears near the top whether or not there are any. `--full` disables
truncation.

**Diff the prose, not the field paths.** A body rewrite arrives as a thousand
`.body[_key=…].children[_key=…].text` entries that no one can judge. Flatten each side's
blocks to text and compare those: it is what distinguishes a real rewrite from pure churn.
On 2026-08-11 that check showed one legal page whose thousand changed fields carried
byte-identical copy, the whole diff being removed spacer blocks and regenerated `_key`s,
while two others silently replaced stale boilerplate with new binding terms.

Check the non-body pushes separately, because they are few and carry the surprises:

```bash
python3 -c "
import json,re
p=json.load(open('$S/plan.json')); D=json.load(open('$S/dev.json'))['fields']; P=json.load(open('$S/prod.json'))['fields']
for k in p['push']:
    if not re.search(r'\.body\[', k): print(k,'\n   PROD',repr(P.get(k,'<absent>'))[:120],'\n   DEV ',repr(D.get(k,'<absent>'))[:120])"
```

A `<absent>` on the development side is a **deletion** heading for production. Confirm each
one is deliberate rather than an artifact of how the development document was re-authored,
and check whether the field is still in the schema and still rendered before assuming its
removal is intended.

Stop here and summarise for the human. On Path B, edit `plan['push']` to hold exactly the
keys to write.

### 3. Back up the documents in scope

```bash
python3 - <<'PY'
import json
docs = ['vendorDashboard', 'homeownerDashboard']       # every document you will touch
raw = {}
for line in open('$S/prod.ndjson'):
    d = json.loads(line)
    if d['_id'] in docs:
        raw[d['_id']] = d
json.dump(raw, open('docs/audits/sanity-sync-<DATE>-production-backup.json', 'w'),
          indent=1, ensure_ascii=False)
PY
```

`docs/audits/`, never the scratchpad.

### 4. Upload any genuinely new artwork

```bash
python3 .claude/skills/sanity-content-sync/scripts/upload_assets.py \
  --plan $S/plan.json --dev $S/dev.json --target production            # dry run, lists files
python3 .claude/skills/sanity-content-sync/scripts/upload_assets.py \
  --plan $S/plan.json --dev $S/dev.json --target production --apply
```

This records the new production ids in `plan['asset_remap']`. Skip it when the comparison
reported no UPLOAD entries.

### 5. Build, dry-run, apply

```bash
python3 .claude/skills/sanity-content-sync/scripts/build_mutations.py \
  --plan $S/plan.json --dev $S/dev.json --prod $S/prod.json --out $S/mutations.json

curl -sS -X POST "https://5z88vhq0.api.sanity.io/v2021-06-07/data/mutate/production?dryRun=true&returnIds=true" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @$S/mutations.json

curl -sS -X POST "https://5z88vhq0.api.sanity.io/v2021-06-07/data/mutate/production?returnIds=true" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @$S/mutations.json
```

The dry run also proves the token can write to the target. Re-export both datasets
immediately before applying and confirm the change set has not moved.

Read the builder's summary before the dry run and reconcile it against the plan. It prints
one `set` or `unset` line per root, which is the last point where a whole-array escalation is
visible as such. A `set body` on a document you expected to change in one paragraph means the
patch is rewriting the entire body.

### 6. Verify and checkpoint

```bash
python3 .claude/skills/sanity-content-sync/scripts/export.py production $S/prod-after.json
python3 .claude/skills/sanity-content-sync/scripts/verify.py \
  --plan $S/plan.json --dev $S/dev.json --before $S/prod.json --after $S/prod-after.json \
  --checkpoint docs/audits/checkpoints/sanity-content.json
```

Five checks, the last being that every asset reference in every touched document resolves.
The checkpoint is written only on PASS. **Commit it.**

Write up what happened in `docs/audits/sanity-sync-<DATE>.md`.

## Traps this tooling already handles, and why

**A key missing from the checkpoint means "absent on both sides", not "unknown".** Both
baseline lookups default to the `<absent>` sentinel. A bare `.get()` returns `None`, which
never equals the hash of the sentinel, so every field development newly created scored as a
CONFLICT. See the note under "How direction is determined": the symptom is a conflict count
in the thousands where production has not been touched.

**The plan is written before the report, and the report must not be piped.** `compare.py`
prints thousands of lines. When the plan was written last, truncating stdout killed the run
with SIGPIPE first and left the *previous* run's `plan.json` on disk while the summary on
screen described the new one. The next step then built mutations from the stale plan. The
ordering is fixed, but redirect to a file rather than piping.

**`set` cannot express a deletion; `unset` can.** A field production holds and development
does not has no common ancestor to patch. `build_mutations.py` emits `unset` for it on the
same document patch, and drops any unset nested inside a subtree already being written whole.
Before this it refused with "no common ancestor", which read as a structural problem rather
than the ordinary field removal it was, and invited a hand-written patch that could break the
one-patch-per-document rule.

**`_system.base.rev` must be excluded.** It changes on every edit. Leaving it in the
comparison makes already-synced documents look drifted; it is what initially made `aboutUs`
and `refer` appear to still differ on 2026-08-10 when they were identical.

**Arrays are addressed by `_key`, never by index.** Editing one portable-text block in the
Studio can re-key its siblings. Index addressing then reports a whole array as changed, and a
patch written against an index can hit the wrong item. A `_key`-only change with identical
text is churn, not an edit: check the text before proposing it.

**Whole-array writes delete the other side's entries.** When a changed leaf exists on one
side only, the patch must escalate to the containing array. `build_mutations.py` refuses to
escalate if the write would also overwrite a field that is not approved, and names it. That
guard is what stops a serviceIcons sync from re-adding an icon production deliberately
deleted, and what protects `vendorDashboard`'s production-only FAQ.

**One patch per document.** Two patches against the same document in one transaction collide
on `ifRevisionID`. The builder groups by document for this reason.

**`ifRevisionID` on every patch.** A concurrent Studio edit then fails the whole transaction
instead of being silently overwritten.

**Uploading changes the asset id.** The CDN does not serve the bytes originally uploaded: a
2026-08-07 upload read 315254 bytes with sha1 `c5a255a6…` in development, while every fetch
returned 269448 bytes hashing to `0a1c0588…`. Production therefore gets a different id and
the datasets diverge permanently on that reference. Prefer CHURN wherever production already
holds the artwork.

**Production is live.** There is no deploy gate. Pages render CMS content with code fallbacks
(`apps/web/src/app/(public)/vendors/page.tsx:155-173`), so adding a section switches a page
from hardcoded copy to CMS copy the moment it lands. Legal pages under `/member-terms`,
`/vendor-terms` and `/bulqit-service-agreement` are binding terms; get human sign-off, not an
inference that development is newer.

**A field whose type changed is coupled to deployed code, and the comparison cannot see it.**
Content and schema ship separately: the schema rides a branch, the content rides this skill.
When a field changes shape the receiving dataset is only safe if every branch that could
serve production renders the new shape. On 2026-08-11 `refer.description` went from `string`
to portable text (BUL-1403). `blue`, `green` and `development` branch on `Array.isArray` and
handle both; `main` still types it `string` and renders it straight into a paragraph, where an
array would crash the page.

Any push where one side is `<absent>` at a leaf while the other has children under the same
parent is a type change wearing a deletion's clothes. Find the field in
`packages/sanity-cms/src/schemas/`, then check its renderer on **every** branch that can
serve the target, not just the one you are working in:

```bash
for b in origin/main origin/blue origin/green origin/development; do
  echo "--- $b"; git show $b:<schema-file> | grep -A4 "name: '<field>'"
done
```

Confirm which slot is actually serving before relying on it. The live deployment carries the
branch alias of the slot that built it, so `npx vercel inspect https://www.bulqit.com` naming
`bul-website-git-green` means green is serving.

**Drafts are not synced.** `drafts.*` documents are excluded. A production draft published
later will overwrite whatever this skill wrote to that document.

**Studio links for review.** `https://bulqit.com/cms/intent/edit/id=<docId>;type=<type>/` for
production, `https://development.example.com/cms/intent/edit/id=<docId>;type=<type>/`
for development. Each deployment's Studio edits whichever dataset its
`NEXT_PUBLIC_SANITY_DATASET` is bound to.
