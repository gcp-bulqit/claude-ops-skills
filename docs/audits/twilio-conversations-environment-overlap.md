# Twilio Conversations: production and preview share one proxy namespace

Analysis of `[connectMemberVendorMessaging]` failures on `staging.example.com` and
`www.bulqit.com`, 2026-07-30.

Source logs:
- `docs/logs/conversations-messaging/bul-website-log-export-2026-07-30T17-24-47.json` (243 entries, 16:53 to 17:09 UTC)
- `docs/logs/conversations-messaging/bul-website-log-export-2026-07-30T17-25-01.json` (7,824 entries, 05:52 to 17:09 UTC)

## Verdict

The hypothesis is correct and now proven, not inferred. Production, preview and
staging point at the **same Twilio account, the same Twilio Conversations Service
and the same 21 phone numbers**, while each writes its proxy bookkeeping to a
**separate Postgres database**. Twilio enforces binding uniqueness across the
whole account; the databases that decide which proxy to hand out cannot see each
other. Every allocation decision is therefore made against an incomplete picture,
and Twilio rejects the result.

The fix is separate phone numbers per environment. Splitting the Conversations
Service does not address it; see the experiment below.

## Evidence chain

**1. The credentials are shared.** Pulled from Vercel and compared byte for byte:

| Variable | Production | Preview | Staging |
|---|---|---|---|
| `TWILIO_ACCOUNT_SID` | `AC4647a2…` | identical | identical |
| `TWILIO_CONVERSATIONS_SERVICE_SID` | `IS40ff66…` | identical | identical |
| `TWILIO_API_KEY_SID` | `SKd2de…` | `SK7409…` | differs |

Different API keys give a false sense of separation. An API key is only a
credential; it scopes nothing. Both keys authenticate into account `AC4647a2…`
and both address Conversations Service `IS40ff66…`.

**2. The phone pool is the same 21 numbers.** `twilio_phone_number_pool` in the
production Neon branch and in the development branch (`ep-morning-pond`) contain
byte-identical rows: the same 21 numbers, the same 5 retired, the same 16
allocatable. The `0/16` in the `sms_pool_alert` payload is that pool.

**3. Ten numbers are claimed as vendor contact lines in both databases, and two
of those name a different vendor on each side.** Compared by `contact_number`
with `vendor_phone` hashed:

```
prod contact lines: 10, dev: 16, claimed in BOTH: 10
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  DIFFERENT vendor each side  <-- collision
  +1XXXXXXXXXX  DIFFERENT vendor each side  <-- collision
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
  +1XXXXXXXXXX  same vendor
```

Every number production has issued as a vendor business line is also issued in
development. Two of them are published to homeowners as two different vendors'
lines at once.

**4. The conversation blocking production lives only in the development
database.** Production's vendor leg failed 83 times with Twilio 50416:

```
A binding for this participant and proxy address already exists in
Conversation CHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

That SID is the conversation *holding* the conflicting binding, not the target.
Looking it up:

- Production `conversations`: **no row**.
- Development `conversations`: **id 480, kind `member_vendor`, created 2026-07-16 23:15:48**.

Development conversation 480 holds an active vendor binding on proxy
`+1XXXXXXXXXX`. Production cannot bind that pair and has no way to discover why,
because the row that explains it is in another database.

This is the complete loop: a thread created on `staging.example.com` two weeks
ago is what breaks messaging provisioning on `www.bulqit.com` today.

## Failure modes in the logs

| Count | Host | Error | Leg |
|---|---|---|---|
| 2,591 | testing | No available phone numbers in pool | homeowner |
| 2,591 | testing | No available phone numbers in pool | vendor |
| 83 | **www** | **Twilio 50416 binding conflict** | vendor |
| 28 | testing | Twilio 50407 invalid binding address | homeowner |
| 28 | testing | `sms_pool_alert` critical, 0/16 | - |
| 14 | testing | Twilio 50416 binding conflict | vendor |

Three distinct problems, one root cause.

### A. Pool exhaustion on preview (5,182 errors, 11 hours continuous)

Development has claimed all 16 allocatable numbers as vendor contact lines. The
17th vendor cannot be provisioned, so `getOrCreateContactNumber` throws from
`allocateNumber` (`phonePoolService.ts:116`). Preview is not exhausted because
preview has too many vendors. It is exhausted because it draws from production's
pool of purchased numbers, and one contact line per vendor with 16 numbers means
16 vendors total across *all* environments combined.

### B. Binding conflict on production (83 errors, 36 minutes)

Covered above. Production is blocked by a preview-created binding.

### C. Twilio 50407, invalid messaging binding address (28 errors, 16:53 to 17:02)

Appears only after development claimed its last contact lines, and only on the
homeowner leg, whose proxy is the vendor's contact line.

The first reading was a pool row for a number Twilio no longer owns. **That is
ruled out.** All 21 pool rows match numbers the account actually owns, checked
against the incoming-numbers listing (see "Resolved" below). So this needs a
different explanation, most likely that one of the late-claimed numbers is owned
but not SMS capable, or that the homeowner address itself failed validation.

Worth noting either way: `retireContactNumberByContactNumber` exists precisely
for "the number no longer exists in Twilio"
(`vendorContactService.ts:273-278`) and nothing calls it on a 50407, so a genuinely
dead number would be retried indefinitely.

## Amplification: the self-heal loop has no backoff

`GET /api/messaging/user/conversations` schedules
`reconcileMissingMemberVendorLegs` in an `after()` callback on every request
(`route.ts:535`). The repair calls `connectMemberVendorMessaging`, which fails,
which leaves the leg missing, which makes the next poll detect it again. The
inbox polls every 30 seconds.

There is no attempt counter, no backoff, and no circuit breaker. Measured from
the logs: **10.1 errors per minute sustained for 11.2 hours on preview** (peak
33/min), and 2.8 per minute on production. Each one is a failed Twilio API call.
A permanent failure is being retried forever at polling frequency.

The detection logic is correct and self-terminating for the case it was designed
for (`reconcileMemberVendorLegs.ts:36-46`: once a leg binds it stops being
flagged). It has no handling for a leg that *cannot* bind.

## Code defects found while tracing this

### 1. A vendor-side proxy can be allocated from another vendor's contact line

`phonePoolService.ts:654-663`. The comment states the intent plainly:

```ts
// Allocate a fresh proxy. Exclude the rejected candidates, every proxy bound or
// reserved for this real phone (Twilio's binding-uniqueness rule), and the vendor's
// shared contact line, which is never allocatable as an individual proxy.
const exclude = new Set<string>(candidates)
exclude.add(vendorContactNumber)
```

It excludes *this pair's* contact line only. Every other vendor's contact line
stays allocatable. `allocateNumber` filters on `status != 'retired'` and the
reserved list, nothing else, so it will return one.

This is not theoretical. Development conversation 480 bound a vendor's real phone
to proxy `+1XXXXXXXXXX`, which is a different vendor's published business line.
The intended invariant ("never allocatable as an individual proxy") is violated
in live data. No test covers `getOrAllocateConversationProxy`; the exclusion set
is unguarded.

Fix: exclude all rows in `vendor_contact_numbers`, not one.

### 2. `allocateNumber` never marks a number in use, and does not lock

`phonePoolService.ts:102-143`. It reads with `findFirst` ordered by phone number
ascending, then updates only `assignedAt`. The `status` column is never set to
`in_use`; `checkPoolHealth` documents this and works around it by counting
`vendor_contact_numbers` instead (`phonePoolService.ts:200-206`).

Two concurrent allocations therefore read the same row and both return the same
number. With a shared pool across environments the window widens from
"concurrent requests" to "any two environments at any time". A `SELECT … FOR
UPDATE SKIP LOCKED` or a conditional update returning the claimed row would close
this.

### 3. Twilio's own error is the only signal that another environment holds a binding

There is no cross-environment visibility by design, which is fine, but the retry
path treats 50416 as a transient race. `messagingService.ts:1695-1720` allocates
a fresh proxy and retries **once**, then rethrows. Against a permanently held
foreign binding, the retry cannot succeed and the second allocation burns another
pool number on the way out.

## Binding uniqueness is account-wide, proven by experiment

An earlier draft of this document claimed a Conversations Service partitions the
proxy namespace, and recommended splitting the service as the fix. **That was
wrong.** Tested directly against account `AC4647a2…` on 2026-07-30, binding the
same pair three times:

| Step | Where | Result |
|---|---|---|
| 1 | Service A (`IS5c9ac7…`), conversation 1 | **201 success**, participant `MB5aa17c…` |
| 2 | Service A, a *second* conversation | **50416**, names conversation 1 |
| 3 | Service B (`IS9d460e…`), a third conversation | **50416**, names conversation 1 |

Step 3 is the answer. Twilio rejected the bind from a **different Conversations
Service** and pointed at a conversation living in Service A. Conflict detection
reaches across services.

All three test conversations were deleted afterwards and verified gone (404).

This matches how inbound routing has to work. The lookup key is the `(From, To)`
pair and nothing else; an inbound SMS carries no Service SID, so resolution
cannot be service-scoped without becoming ambiguous. Twilio's own wording, with
no service qualifier: "Only one Conversation ... can bind a number pair together."
The code comment at `conversationsGateway.ts:51-55` already said "in the
account" and was correct.

**Consequence: two Conversations Services cannot share a phone number for two
environments.** Separating environments requires separating numbers.

## Recommended solution

### The fix: separate phone numbers per environment

This is the only change that stops failure mode B. Two options:

**Option A, separate number blocks in one account.** Purchase a distinct block
for non-production and make `twilio_phone_number_pool` environment-specific.
Delete production's 21 numbers from the preview database, and keep non-production
numbers out of production's. One Twilio bill, one console. Cheapest correct
answer.

**Option B, a separate Twilio subaccount for non-production.** Numbers are owned
per account, so a subaccount isolates the namespace structurally rather than by
convention, and no future seed script can reintroduce the overlap. More
credentials to manage, strictly safer.

Prefer B if non-production testing is expected to keep growing. Prefer A to move
fastest.

Either way the current state needs correcting: the preview database should not
contain production's 21 numbers.

### Also worth doing: split the Conversations Service

This does **not** fix the binding conflicts. It is still worth doing, for
different reasons: conversation SIDs stop colliding across environments (the
`CH625494f3…` lookup that made this diagnosis slow), webhooks get scoped per
environment, and it is Twilio's documented pattern for "multiple, distinct
environments (such as dev, stage, and prod) under a single account."

The services already exist and were never wired up:

```
ISxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  ConversationService_development  <- prod, preview AND staging
ISxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  ConversationService_staging      <- unused
ISxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  Default Conversations Service
ISxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  Flex Conversation Service
ISxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  MyNewChatService
```

Production runs on the service named `ConversationService_development`. There is
no production service; one needs creating.

1. Create a production `IS…` service; point staging at the existing
   `ConversationService_staging`.
2. Point each service's webhook at that environment's
   `/api/twilio/conversations/webhook`.
3. Set `TWILIO_CONVERSATIONS_SERVICE_SID` per environment in Vercel, and on the
   matching Fly worker apps. Worker secrets are the source of truth there and
   need setting separately; a Vercel change does not reach them.
4. Migrating production onto a new service orphans existing conversations, which
   live under the old service SID. Sequence this carefully, or leave production
   on `IS40ff66…` and move only the non-production environments off it.

Note the fallback at `conversationsGateway.ts:171-198`: on a 404 the gateway
retries against the account default service. Once services are split, that
fallback will silently pull a preview call into the account default namespace. It
already logs a warning. It should become an error after the split, or be removed.

### Capacity

16 allocatable numbers with a one-contact-line-per-vendor model caps the platform
at 16 vendors. Production has already claimed 10 of them. Even with environments
separated, production is 6 vendors from the same exhaustion preview is in now.
The `sms_pool_alert` fires at 0 available (`SMS_POOL_CRITICAL_THRESHOLD` defaults
to `0`), which is after allocation has already thrown. Raise the critical
threshold above zero so the alert leads the failure instead of confirming it.

### Code changes worth making regardless

1. Exclude all vendor contact lines when allocating a fresh proxy
   (`phonePoolService.ts:657`). This is a correctness fix with live bad data
   behind it, and it should carry a test.
2. Bound the self-heal retry. Track consecutive failures per conversation and
   stop after a few, or gate the reconcile behind a timestamp so a permanently
   broken thread is attempted at most once an hour rather than twice a minute.
   The current loop turned one unfixable thread into 5,182 Twilio calls in a day.
3. Claim pool numbers atomically (`SELECT … FOR UPDATE SKIP LOCKED`, or a
   conditional update that returns the row) and actually write `status`.
4. On a 50407, retire the offending pool number instead of retrying it. The
   method already exists and is never called.

### Verification after the change

The number separation is what fixes the outage; check it first.

- No phone number appears in more than one environment's
  `twilio_phone_number_pool`.
- No `contact_number` appears in more than one environment's
  `vendor_contact_numbers`.
- `[connectMemberVendorMessaging]` 50416 errors stop on `www.bulqit.com`.
- Preview pool availability rises above zero once it draws from its own numbers.

For the service split:

- `TWILIO_CONVERSATIONS_SERVICE_SID` differs across production, preview and
  staging in Vercel and on both Fly worker apps.
- No `CH…` SID resolves in more than one environment's `conversations` table.
- No new `[twilio] Conversations call not found in service` warnings.

## Resolved: the pool numbers Twilio actually owns

Account `AC4647a2…` owns exactly 21 incoming phone numbers, matching the 21 rows
in both databases one for one. There is no separate non-production block to fall
back on, so Option A above means purchasing numbers, not reassigning existing
ones.

Every pool row does correspond to a number the account owns, so failure mode C
(50407) is **not** explained by a pool row for a released number. It needs
another explanation. Worth noting from the same listing: most pool numbers still
carry `smsUrl = https://demo.twilio.com/welcome/sms/reply`, the Twilio demo
default, and none has a Messaging Service. They work only because an active
Conversations binding intercepts routing ahead of the number's own webhook. A
number without a live binding drops inbound SMS.
