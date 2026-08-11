# Sanity content merge: development -> production (APPLIED 2026-08-07)

Applied in two steps. First, four `patch` mutations in one transaction (`K6F4OI1k43wrKyNbvvqrIr`),
each guarded by `ifRevisionID` and targeted with `_key`-based paths. Then an asset upload and a
single append to `serviceIcons` (`XkAJwzM7ESAzqiqRL9apr2`), described under "serviceIcons" below.

Project `5z88vhq0`, datasets `development` (274 docs) and `production` (287 docs before, 288
after the upload). Pre-change copies of the five documents in scope:
[`sanity-sync-2026-08-07-production-backup.json`](sanity-sync-2026-08-07-production-backup.json).

Direction was development -> production only. Where production held content development lacks,
the patch was scoped to leave it in place rather than overwrite the parent array.

## What landed

| Document | Field | Change |
| --- | --- | --- |
| vendorDashboard | `opportunitiesSection` | added; production had none |
| vendorDashboard | `platformSection` | added; production had none |
| vendorDashboard | `recurringSection` | added; production had none |
| vendorDashboard | `processSection.cards[0e9a264a510f].body` | "recurring contracts" -> "contracted homeowners" |
| vendorDashboard | Block-size FAQ answer | see below |
| homeownerDashboard | services-list FAQ answer | "landscaping" -> "gutter care", ordering shifts |
| homeownerDashboard | pricing FAQ answer | one paragraph -> two, adding the flat-rate case |
| homeownerOnboardingFAQs | services-list FAQ answer | same edit as homeownerDashboard |
| homeownerOnboardingFAQs | pricing FAQ answer | same edit as homeownerDashboard |
| vendorOnboardingFAQs | Block-size FAQ answer | see below |

The Block-size answer appears in two documents and changed the stated figures:

- production before: "a tight geographic area (about 1 square mile) with roughly 500 to 1,000 single-family homes"
- production now: "a tight geographic area with roughly 1,000-2,000 single-family homes"

That is a factual product claim, not a copy tweak. It reads as deliberate in development, where
both documents agree, but it is worth confirming with whoever owns the number.

The three new vendorDashboard sections are a visible change to the live vendors page. It renders
them with `?? OPPORTUNITIES_FALLBACK` / `RECURRING_FALLBACK` / `PLATFORM_FALLBACK`
(`apps/web/src/app/(public)/vendors/page.tsx:155-173`), so production was showing hardcoded copy
for all three and now shows the CMS content instead.

## The asset blocker did not apply this time

The 2026-08-03 analysis found that a document copy would break images, because Sanity asset ids
are content-derived and the same artwork was uploaded separately into each dataset. That still
holds in general, but not for what was synced here.

All five asset references inside the three new sections already exist in production, so they were
copied verbatim and resolve. Verified after applying: vendorDashboard went from 15 references to
20, all five additions resolve in production, and **no document lost a reference**.
homeownerDashboard kept all 24 of its own references, so production's `-avif` service icons were
not replaced by development's `-heif` uploads of the same bytes.

The four FAQ answer arrays that were replaced wholesale contain no asset references at all, which
is why replacing them was safe.

## serviceIcons: one icon uploaded and appended

Both datasets held 20 icons and shared 18. Development had *Mobile Pet Grooming* and *Air
Conditioning Maintenance*; production had *HVAC* and *Dryer Vent Cleaning*, added 2026-08-06.
*Air Conditioning Maintenance* and *HVAC* are the same service under two names, so the decision at
review was to keep production's *HVAC* and drop development's *Air Conditioning Maintenance*.

Only *Mobile Pet Grooming* was carried over, which meant only `pet-removebg-preview.png` needed
uploading. `AC-removebg-preview.png` was not uploaded, since nothing would reference it.

Production now holds 21 icons: the 18 shared, its own *HVAC* and *Dryer Vent Cleaning*, and
*Mobile Pet Grooming*. The append used `insert: {after: 'icons[-1]'}` rather than replacing the
array, so no production entry was touched.

### The uploaded asset has a different id from development's

Asset ids are the sha1 of the file, and the bytes served by the CDN are not the bytes originally
uploaded. Development's asset record reports 315254 bytes with sha1 `c5a255a6...`, but the CDN
returns 269448 bytes hashing to `0a1c0588...`, consistently and regardless of `?dl=` or an
authenticated request. The original is not retrievable through the CDN.

So production's copy is `image-0a1c0588...-526x474-png`, not development's
`image-c5a255a6...-526x474-png`. Same image, same 526x474 dimensions, different document. The
appended entry carries the production ref, verified to resolve.

This is the asset-churn pattern from the 2026-08-03 analysis, created deliberately this time and
in the only direction available. `serviceIcons` will therefore keep showing an asset-reference
difference between the datasets, on top of the *Air Conditioning Maintenance* entry that
development still holds.

## What was deliberately left alone

**vendorDashboard's production-only FAQ**, "What kind of service vendors is Bulqit looking for?"
(`_key=a80d1262c483`). Development has no counterpart. Patching individual fields rather than the
whole `faq` array left it intact; a wholesale copy would have deleted it. Confirmed present after
applying.

**homeownerDashboard's "What is Bulqit and how does it work?" answer.** The two datasets differ
only in the block's `_key`; the text is byte-identical at 276 characters. Writing it would churn
keys for no content change, so it was skipped. It will keep surfacing in comparisons as a
difference.

**Eight documents that differ only by asset-reference churn**: aboutUs, homeownerOnboardingPricing,
homeownerOnboardingServices, homeownerOnboardingWelcome, pricingAllocationExample,
vendorOnboardingVerification, vendorOnboardingWelcome, vendorRegistrationThankYou. Same artwork,
different upload ids. Syncing them would break images and change nothing a visitor reads.

`aboutUs` and `refer` are now fully in sync; their only remaining difference is
`_system.base.rev`, an internal revision field.

## Verification

Production was re-exported after the transaction and compared against development:

- homeownerOnboardingFAQs and vendorOnboardingFAQs: zero text differences.
- vendorDashboard: one remaining difference, the production-only FAQ above.
- homeownerDashboard: two remaining, both the `_key`-only churn above.
- No asset reference was removed from any document.

After the `serviceIcons` step, re-exported again: production holds 21 icons, *Mobile Pet
Grooming*'s reference resolves, *HVAC* and *Dryer Vent Cleaning* are intact, and the only
documents touched were `serviceIcons` and the new asset record. The remaining difference against
development on this document is *Air Conditioning Maintenance* plus the pet-grooming asset id.

## Method note

The comparison excludes `_rev`, `_updatedAt`, `_createdAt` and `_system`. Leaving `_system` in
reports `_system.base.rev` on every edited document and makes already-synced documents look
drifted; that is what initially made `aboutUs` and `refer` appear to still differ.

Array comparison matches items by `_key`, not by index. Without that, a re-keyed portable-text
block reads as a delete plus an insert, and a genuine rewrite is indistinguishable from churn.
