---
name: twilio-pool-numbers
description: "Buy Twilio masking-pool phone numbers for the production or testing environment, or move numbers between the two. Use when the SMS pool is exhausted, when the sms_pool_alert fires, when 'No available phone numbers in pool' appears in logs, or when an environment needs more member/vendor masking numbers. Examples: \"add 5 numbers to the testing pool\", \"production is out of proxy numbers\", \"buy more masking numbers\""
---

# Adding numbers to a Twilio masking pool

Every environment authenticates into **one** Twilio account (`AC4647a2…`), and that
account lists every number anyone has ever bought. Ownership is therefore a convention
enforced by database rows and an allowlist, not by anything Twilio does for you. Getting
the order wrong hands one environment's numbers to another.

Background: [`docs/audits/twilio-conversations-environment-overlap.md`](../../../docs/audits/twilio-conversations-environment-overlap.md), BUL-1323.

## The rule that matters most

**Write the `retired` rows into the *other* environment's pool BEFORE buying.**

`/api/cron/phone-pool-sync` runs hourly in production, walks every number the account
owns, and adopts anything missing from its pool. Buy first and production ingests the new
numbers within the hour. This is not hypothetical: on 2026-07-30 the cron adopted 3 of 5
new testing numbers **four seconds** after they were purchased.

A `retired` row is what shields them. The cron skips a number it already has
(`existingSet.has()`), and skips the voice rewrite for retired rows, and `allocateNumber`
filters `status != 'retired'`. One row closes all three.

## Which numbers belong to whom

| Environment | Area codes | Database | Pool rows |
|---|---|---|---|
| Production | 213, 310, 312, 737, 747, 818, 855 | prod Neon | own numbers `available`, testing's `retired` |
| Testing / preview | **628** | `ep-morning-pond` | own numbers `available`, production's `retired` |

Keep testing on **628**. Production uses none of that range, so any `+1628…` is
recognisable as a testing number at a glance. That readability is worth preserving.

Both environments hold rows for **all** account numbers. They differ only in which ones
they may allocate. Never delete another environment's numbers from a pool: reuse consults
assignment history before the pool, and an absent row and a healthy row were once
indistinguishable there. Retire, do not delete.

## Procedure

Credentials: `vercel env pull <file> --environment=preview` gives `TWILIO_ACCOUNT_SID` and
`TWILIO_AUTH_TOKEN`. Production's auth token is sensitive and pulls empty; preview's works
for the whole account.

### 1. Search, and check the clock

```bash
curl -s -u "$AS:$AT" \
  "https://api.twilio.com/2010-04-01/Accounts/$AS/AvailablePhoneNumbers/US/Local.json?AreaCode=628&SmsEnabled=true&VoiceEnabled=true&PageSize=12"
```

Only US local numbers with **both** SMS and voice pass the sync filter
(`/^\+1\d{10}$/` plus `capabilities.sms && capabilities.voice`); anything else is skipped
silently and never enters the pool. Note how long until the top of the hour. Twilio does
not reserve numbers, so a shortlist can be bought out from under you; re-search if slow.

### 2. Shield them in the OTHER environment first

```sql
select setval('twilio_phone_number_pool_id_seq', (select max(id) from twilio_phone_number_pool));
insert into twilio_phone_number_pool (phone_number, friendly_name, status) values
  ('+1628…','TESTING block - not for production use','retired'), …
on conflict (phone_number) do update
  set status='retired', friendly_name=excluded.friendly_name, updated_at=now();
```

The `setval` is not optional. This table has a history of sequence drift where the id
sequence sits behind `max(id)`, and every insert fails with a duplicate-key error until it
is reset. While drifted, the cron's own `addNumber` throws and the outer `try/catch` turns
that into a 500 for the entire sync, including the voice reconciliation that follows.

Rows for numbers you have not bought yet are harmless: they are `retired`, so nothing can
allocate them. Delete any whose purchase then fails.

### 3. Buy

```bash
curl -s -u "$AS:$AT" -X POST "https://api.twilio.com/2010-04-01/Accounts/$AS/IncomingPhoneNumbers.json" \
  --data-urlencode "PhoneNumber=$NUM" \
  --data-urlencode "FriendlyName=Bulqit TESTING pool $N" \
  --data-urlencode "SmsUrl=" --data-urlencode "VoiceUrl="
```

**Set `SmsUrl` and `VoiceUrl` explicitly empty.** Twilio otherwise assigns
`https://demo.twilio.com/welcome/sms/reply`, which is how most production pool numbers
ended up pointing at Twilio's demo endpoint. Do not inherit that.

There is no confirmation step: the POST **is** the purchase and bills immediately. Count
owned numbers before and after and assert the delta.

### 4. Add to the owning environment's pool

Same insert as step 2 with `status='available'` and a `Bulqit <ENV> pool N` friendly name.
Run the `setval` again; it is a different database.

### 5. Voice routing

```
production  https://www.bulqit.com/api/twilio/voice/proxy
testing     https://staging.example.com/api/twilio/voice/proxy?x-vercel-protection-bypass=<token>
```

POST `VoiceUrl` and `VoiceMethod=POST` to each number's `IncomingPhoneNumbers/<PN>.json`.

**Do not reach for the `configure-voice` admin endpoint to do this.** It builds the URL
from `getConfiguredBaseUrl()`, which reads `NEXT_PUBLIC_SITE_URL` first, and that variable
is inlined at **build** time. A preview deployment that has not been rebuilt since the
variable was last corrected still emits production's host, and running the endpoint will
point testing's numbers at production. Set the URL directly, or rebuild preview first.

Production is exempt from Vercel Deployment Protection and takes no bypass token. Preview
needs one; verify it with a probe, where **302 means blocked** by the sign-in wall and any
4xx means the request reached the app.

### 6. A2P registration

Attach every number to the Proxy A2P Messaging Service, or carriers filter its SMS:

```bash
curl -s -u "$AS:$AT" -X POST \
  "https://messaging.twilio.com/v1/Services/MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/PhoneNumbers" \
  --data-urlencode "PhoneNumberSid=$PN"
```

Campaign `QE2c6890…`, status VERIFIED, use case PROXY. Confirm with
`GET /v1/Services/<MG>/Compliance/Usa2p`.

### 7. Update the allowlist

`TWILIO_POOL_NUMBERS` names the numbers an environment may adopt and whose call routing it
may rewrite. Each environment lists **only what it owns**, including its own retired
numbers, and never the other's.

The Vercel CLI cannot set a Preview variable for all branches non-interactively; it returns
`action_required / git_branch_required` and ignores `--yes`. Use the REST API so the
variable applies to every preview branch rather than one:

```bash
curl -s -X POST "https://api.vercel.com/v10/projects/$PROJ/env?teamId=team_TMMQ7TzYusSR1LHVC1dBA74i&upsert=true" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"key":"TWILIO_POOL_NUMBERS","value":"+1…,+1…","type":"plain","target":["preview"]}'
```

Token: `~/.local/share/com.vercel.cli/auth.json`. Use `"type":"plain"` so the value stays
readable; Vercel defaults production and preview variables to sensitive, which makes them
write-only and unverifiable forever. Phone numbers are not secrets.

Comma or whitespace separated. A value naming no number reads as unset rather than as an
empty allowlist.

## Verify

Assert each of these, do not eyeball them:

- The owning pool gained exactly N allocatable rows, all in its own area code.
- The other pool gained exactly N rows, all `retired`, and its allocatable count is
  **unchanged**.
- No number is allocatable in more than one environment.
- The two `TWILIO_POOL_NUMBERS` values share no number. Overlap is the whole bug.
- Every allowlisted number is actually owned by the account, and the only owned numbers
  excluded are the other environment's.
- Free capacity for vendor-side proxies is `allocatable − vendor_contact_numbers`. Every
  vendor permanently claims one number as its published business line, so a pool where
  every number is a contact line has **zero** headroom and cannot provision a new pair,
  even though it looks full.

## Costs

US local is $1.15/month each, toll free $2.15. Confirm against
`https://pricing.twilio.com/v1/PhoneNumbers/Countries/US` rather than quoting from memory.

## Notes

- Retired and reserved numbers stay in **both** pools. `RESERVED_NUMBER_VALUES`
  (`packages/messaging/src/twilio/reservedNumbers.ts`) are platform IVR and support lines
  and are never allocatable to a masking pair.
- The `status` column gates only `retired`. A number marked `in_use` is still claimable,
  because one number legitimately serves many pairs: a vendor's contact line is the proxy
  for every homeowner reaching that vendor.
- The Fly workers allocate from the pool but never run the sync, so they need no
  `TWILIO_POOL_NUMBERS`. They do need the right `TWILIO_CONVERSATIONS_SERVICE_SID`, which
  is set independently of Vercel and has drifted before. Read the live value with
  `flyctl ssh console -a <app> -C 'printenv <VAR>'`; `flyctl secrets list` shows digests
  only and cannot tell you what a value is.
