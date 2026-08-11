# Sanity dataset comparison: development vs production

**Status 2026-08-03: the four text patches below were applied to production.** serviceIcons was
deliberately skipped and remains the only editorial drift left. Everything else that still differs
is asset-reference churn, intentionally left alone.

Applied as four `patch` mutations in one transaction (`PnRpwD9n3gt4wKhN8WqJbl`), each guarded by
`ifRevisionID` and targeted with `_key`-based paths, so no array index or asset reference was
rewritten. Verified afterward: all four fields equal development, and every document's asset
reference set is byte-identical to its pre-patch state (aboutUs 3, homeownerDashboard 24, refer 1,
vendorDashboard 20). Pre-change copies of the four documents:
[`sanity-sync-2026-08-03-production-backup.json`](sanity-sync-2026-08-03-production-backup.json).

The analysis below is the pre-change state, kept as the record of why the scope was narrowed.



Date: 2026-08-03. Project `5z88vhq0`. Datasets `development` (274 docs) and `production`
(285 docs), both `aclMode=public`. Exported via the data export API and compared document by
document, excluding `_rev`, `_updatedAt`, `_createdAt`.

Nothing has been written to either dataset. Unlike the Neon sync, I am not recommending a
wholesale development -> production copy. The reason is in "The blocker" below.

## Headline

| Bucket | Count | Verdict |
| --- | --- | --- |
| Published docs with genuine editorial drift | **5** | Candidates to sync |
| Published docs differing only by asset-reference churn | **7** | Do not sync |
| Shared asset docs differing only by `uploadId` | 73 | Not drift |
| Shared asset docs otherwise identical | 92 | Not drift |
| Assets present in only one dataset | 43 dev / 52 prod | Mostly duplicate uploads of the same art |
| System docs (`system.*`, `sanity.previewUrlSecret`) | 3 | Never sync |
| Drafts | 4 shared and identical, 1 prod-only | See "Drafts" |

## The blocker: asset references do not survive a copy

Sanity asset ids are derived from file content, and the same artwork was uploaded separately into
each dataset, so the two datasets hold **different ids for the same image**. Of the 37 differing
asset references across these documents, 35 point at a file with an identical filename and
identical dimensions on both sides. `homes.svg` is `image-0880a53c...` in development and
`image-1d94b3e8...` in production. Same picture, different document.

Copying a development document into production therefore rewrites a working reference into one
that does not resolve. **23 distinct dev-only assets are referenced by these 12 documents**, and
every one of them would become a dangling reference, rendering as a broken image:

| Document | Dev refs missing from production |
| --- | --- |
| vendorOnboardingVerification | 5 of 5 |
| vendorRegistrationThankYou | 4 of 4 |
| homeownerOnboardingPricing | 1 of 1 |
| homeownerOnboardingServices | 1 of 1 |
| vendorDashboard | 7 of 19 |
| homeownerDashboard | 6 of 24 |
| pricingAllocationExample | 3 of 7 |
| serviceIcons | 2 of 21 |
| homeownerOnboardingWelcome | 1 of 6 |
| vendorOnboardingWelcome | 1 of 5 |

Any sync has to either upload the missing assets to production first and remap each `_ref`, or
keep production's existing refs and copy only the non-asset fields. The second is far safer and
is what I recommend.

Note also `homeownerDashboard`: development's six service icons are `-heif` documents while
production's are `-avif`, with matching content hashes. Development is pointing at an
older/mis-typed upload of the same bytes. Production's refs are the better ones here.

## Direction is not uniformly dev-forward

Nearly every production document carries `_updatedAt = 2026-07-15T21:44:15/16`, which is a bulk
import into production on that date rather than 50 individual edits. Several development
documents predate it and have no editorial change at all:

| Document | dev `_updatedAt` | prod `_updatedAt` | Only difference |
| --- | --- | --- | --- |
| vendorRegistrationThankYou | 2025-10-29 | 2026-07-15 | asset refs |
| vendorOnboardingVerification | 2025-11-22 | 2026-07-15 | asset refs |
| pricingAllocationExample | 2025-12-05 | 2026-07-15 | asset refs |
| vendorOnboardingWelcome | 2026-02-16 | 2026-07-15 | asset refs |
| homeownerOnboardingServices | 2026-03-16 | 2026-07-15 | asset refs |
| homeownerOnboardingWelcome | 2026-07-11 | 2026-07-15 | asset refs |
| homeownerOnboardingPricing | 2026-07-13 | 2026-07-15 | asset refs |

These seven are pure churn. Syncing them breaks images and changes nothing a visitor would read.
**Skip all seven.**

## The five documents with real editorial drift

### 1. homeownerDashboard (dev 2026-07-29, prod 2026-07-15) -- the substantive one

Hero, production -> development:

- title: `["Join your Block.", "Bulk home services.", "Just Bulqit."]` -> `["Home services,", "without the shopping."]` (drops the third line)
- description: "We group nearby homes to get better deals on lawn, pool, and outdoor care." -> "Join your neighborhood to unlock trusted recurring home services at lower prices by helping vendors spend less time driving."

First FAQ answer, production currently reads:

> We partner with established, professional service businesses who want predictable revenue
> growth. Specifically, we're looking to help deliver services in high demand like Lawn Care,
> Pest Control ... Vendor Requirements: Licensed & insured with current certificates...

That is vendor-recruitment copy sitting in the homeowner dashboard FAQ. Development has
homeowner-appropriate copy ("Bulqit is the world's first platform for bulking home services...").
This looks like a production content bug that the sync would fix. Worth confirming with whoever
owns the copy.

Caveat: the six service icons in this document are the heif/avif pair described above. Sync the
text, keep production's icon refs.

### 2. refer (dev 2026-08-03, prod 2026-07-21)

`description`, production -> development:

- prod: "We're building a referral program to reward neighbors who help spread the word. During this early phase, every registration helps your Block launch faster and supports a local charity."
- dev: "Bulqit connects neighbors to collectively purchase home services like landscaping, pest control & pool maintenance. Lower prices, better service, stronger communities."

These are not two drafts of one message. Production explains the referral program; development is
generic positioning copy. Confirm which one belongs on the refer page before syncing. Development
was edited today, so it is probably the intended one, but the content moved away from the page's
apparent subject.

### 3. serviceIcons (dev 2026-07-23, prod 2026-07-15)

Development adds two icons production does not have: **Mobile Pet Grooming** and **Air
Conditioning Maintenance**. Both reference dev-only PNGs (`pet-removebg-preview.png`,
`AC-removebg-preview.png`). This is the one case where the assets must be uploaded to production
first; there is no production equivalent to point at.

### 4. aboutUs (dev 2026-07-29, prod 2026-07-15)

One paragraph gains a trailing period and space. Otherwise only two icon refs that resolve to the
same 80x68 SVGs. Cosmetic.

### 5. vendorDashboard (dev 2026-07-28, prod 2026-07-15)

`opportunitiesSection.columns[0].title` drops a U+2028 line separator: "We bring the opportunities
<LS>to you." -> "We bring the opportunities to you." That character forces a line break in the
rendered heading, so this is a deliberate layout change. The document's other seven differences
are asset churn.

## Drafts

Four drafts exist in both datasets and are byte-identical: `drafts.homeownerRegistrationServices`,
`drafts.howItWorks`, `drafts.referSave`, `drafts.termsOfService`.

Production has one extra: **`drafts.vendorDashboard`** -- unpublished work in production with no
development counterpart. A document sync does not touch drafts, but publishing that draft later
would overwrite whatever this sync writes to `vendorDashboard`. Check what it contains first.

## Recommendation

Do not run a dataset-level copy or `sanity dataset export/import`. Instead, patch the specific
fields, leaving every asset reference in production alone:

1. `homeownerDashboard` -- `hero.title`, `hero.description`, `faq[0].answer` (text only)
2. `refer` -- `description`, once you confirm which copy is correct
3. `vendorDashboard` -- `opportunitiesSection.columns[0].title`
4. `aboutUs` -- the one paragraph, if you care about the trailing period
5. `serviceIcons` -- upload the two PNGs to production, then append the two icon entries with the
   new production asset ids

That is four text patches plus one asset upload, versus 12 documents and 23 broken images from a
naive copy.

Prerequisite for applying: the local `SANITY_API_READ_TOKEN` is the robot "Development and Preview
(Robot)" with role `write`. It reads production fine, but whether its grants cover writes to the
production dataset is unverified. Confirm before relying on it.
