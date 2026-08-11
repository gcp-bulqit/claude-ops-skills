# Sanity content merge: development -> production (APPLIED 2026-08-10)

Applied as four `patch` mutations in one transaction (`KmkMw2CM7j4yWChl7F4ZBb`), each guarded by
`ifRevisionID`. Project `5z88vhq0`, datasets `development` (276 docs) and `production` (291).
Pre-change copies of the seven documents in scope:
[`sanity-sync-2026-08-10-production-backup.json`](sanity-sync-2026-08-10-production-backup.json).

## The headline: /member-terms was serving placeholder text

Production's `/member-terms` page held a five-block body, three blocks of which were Lorem ipsum.
Development held 152 blocks of real legal copy opening "Last Updated: August 9, 2026", authored
2026-08-10. Neither version references any asset, so the replacement was pure text.

Production now carries all 152 blocks with zero Lorem ipsum.

## What landed

| Document | Path | Change |
| --- | --- | --- |
| Member Platform Terms | `/member-terms` | whole document forced from development: body 5 -> 152 blocks, title "Member Platform Agreement" -> "Member Platform Terms", `metadata.title` and `metadata.description` to match |
| Vendor Platform Terms | `/vendor-terms` | rename only: "Vendor Platform Agreement" -> "Vendor Platform Terms", plus both metadata fields. Body was already identical ignoring `_key` |
| Member and Vendor Service Agreement | `/bulqit-service-agreement` | rename only: "Service Agreement" -> "Member and Vendor Service Agreement", plus both metadata fields. Body already identical |
| refer | | whole document forced from development |

`refer` gained referral-program copy on `description` and `metadata.description`: the Environmental
Defense Fund donation and accumulating referral rewards.

**`refer.form.initialTitle` is now a single space.** Development holds `' '` where production held
"Register now to get early access and be the first to hear when the full program rolls out." The
whole-document force was requested after this was flagged, so the blank is intentional, but it is
the one field here that removes copy from a live page. Reversing it means writing that sentence
back to `form.initialTitle`; every other `form` field was already identical.

## Verification

Production was re-exported and compared against development. The four documents dropped out of the
difference list entirely. Asset counts are unchanged on all four and every reference resolves
(`refer` keeps its single reference; the three pages have none). The transaction touched only those
four documents; `serviceIcons` was confirmed untouched at 19 icons.

## serviceIcons: left alone, and production moved against it

Not synced, as instructed. The reason it should stay that way is worth recording.

On 2026-08-10 at 17:43:45, sixteen seconds after a development edit at 17:43:29, production's
`serviceIcons` went from 21 entries to 19. Two were removed: **Dryer Vent Cleaning** and **Mobile
Pet Grooming** -- the latter being the entry appended by the 2026-08-07 sync. The `lawn-care` entry
was also re-keyed from `41cd77e9d6ea` to a 32-character key, which is the shape a Sanity Studio
array edit produces.

Development still holds Mobile Pet Grooming and Air Conditioning Maintenance. Syncing this document
would re-add an icon production had just deleted, and re-add the Air Conditioning Maintenance entry
that was deliberately dropped on 2026-08-07.

The PNG uploaded to production on 2026-08-07 (`image-0a1c0588...-526x474-png`) is still in the
dataset and is now unreferenced.

## vendorDashboard and homeownerDashboard: reviewed and closed, not synced

Both were reviewed in full and left as they stand. Production is current on every remaining
difference; the detail below is the record of why each one was left.

### vendorDashboard -- one text difference, ten asset differences

The text difference is production-only content: the FAQ "What kind of service vendors is Bulqit
looking for?" (`_key=a80d1262c483`), which development has no counterpart for. A whole-document
force would delete it.

Eight of the ten asset differences are the same artwork under two ids -- identical filename, identical
dimensions, identical byte size:

| Field | File | Dimensions |
| --- | --- | --- |
| processSection card icons (3) | document-approved.svg, document-happy.svg, payment-time.svg | 41x40 |
| serviceStepsSection step icons (5) | document-approved.svg, map-lock.svg, money-approved.svg, map.svg, squares-mesh.svg | 40x40 |

None of those eight development ids exists in production, so copying them produces eight broken
images for zero visible change.

The remaining two are genuinely different files, and here development's ids **do** exist in
production, so either could have been synced with a one-field patch and no upload:

| Field | development | production |
| --- | --- | --- |
| `calculatorSection.chartMobile` | `..._mobile-trimmed2.png`, 390x390, 67317 B | `..._mobile-trimmed2 (1).png`, 390x390, 65337 B |
| `hero.audio` | `liam.mp3`, 514134 B | `audio_0Jzquw6MtpCMLtdKa3en.mp3`, 855607 B |

The hero audio is a different recording, not a re-encode of the same one.

**Resolved at review: production holds the current values, and neither field was synced.**
Development is behind on both. The difference is expected to persist and should not be read as
drift needing action on a future run.

### homeownerDashboard -- two text differences, six asset differences

The text differences are a single FAQ answer, "What is Bulqit and how does it work?"
(`_key=321628a93f6e`), where the two datasets differ **only** in the block's `_key`. The text is
byte-identical at 276 characters. Writing it churns keys for no content change.

All six asset differences are the same image with a mis-typed extension. The sha1 is identical on
both sides; only the suffix differs:

| Service | development | production |
| --- | --- | --- |
| Lawn Care | `image-e082b12e...-96x96-heif` | `image-e082b12e...-96x96-avif` |
| Pool Maintenance | `image-cf84f8f6...-96x96-heif` | `image-cf84f8f6...-96x96-avif` |
| Pest Control | `image-521c9ab8...-96x96-heif` | `image-521c9ab8...-96x96-avif` |
| Outdoor Cleaning | `image-3a3c3f40...-96x96-heif` | `image-3a3c3f40...-96x96-avif` |
| Window Cleaning | `image-43cf6577...-96x96-heif` | `image-43cf6577...-96x96-avif` |
| Trash Bin Cleaning | `image-74f45ff9...-96x96-heif` | `image-74f45ff9...-96x96-avif` |

Same bytes, registered under the wrong type in development. Production's `-avif` references are the
correct ones, so this drift should be fixed in development rather than synced forward.

## Still not synced, unchanged from 2026-08-07

Eight documents differing only by asset-reference churn: aboutUs, homeownerOnboardingPricing,
homeownerOnboardingServices, homeownerOnboardingWelcome, pricingAllocationExample,
vendorOnboardingVerification, vendorOnboardingWelcome, vendorRegistrationThankYou.

The extra drafts in each dataset are `sanity.previewUrlSecret` system documents, not content.
