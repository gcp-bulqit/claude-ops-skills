# Twilio Unauthenticated Routes — Coverage Summary (BUL-86)

Maps each previously-exposed `/api/messaging` route and each source ticket to the
test that proves it is closed. Evidence here is automated-test-based: the negative
regression suite asserts the exact 401/403 the fix introduces. Happy-path access is
covered by the helper unit tests and each route's existing tests.

**Branch:** `bul-86-exposed-messaging-endpoint-vulnerability`
**Verified:** `pnpm --filter web check-types` clean; messaging + utils suites green
(533 tests), including the new files below.

## Auth pattern

Member-facing routes follow the app's established inline pattern (matching `join` /
`identities` / `read`): `auth()` → null guard (401) → `resolveConversationAccess`
→ `conversationAccessErrorResponse`, plus an inline impersonation check on mutations.
Admin-only routes use the existing `withApiAdminAuth` wrapper. No new auth wrapper is
introduced; the one shared primitive is the authorization rule itself.

## Primitives

| Primitive | File | Tests |
|---|---|---|
| `resolveConversationAccess` (member_vendor / support / legacy + admin bypass) | `apps/web/src/server/messaging/authorization.ts` | `apps/web/src/server/messaging/_tests/authorization.test.ts` (11) |
| `conversationAccessErrorResponse` (404 not_found / 403 not_participant) | `apps/web/src/server/messaging/authorization.ts` | exercised via the routes |
| `isAdminUser` (boolean admin check) | `apps/web/src/utils/security.ts` | exercised via the above |

## Route matrix and proof

Regression assertions live in
`apps/web/src/app/api/messaging/_tests/auth-regression.test.ts` (30).

| Route | Method | Before | After (guard) | Proof |
|---|---|---|---|---|
| `conversations/[sid]/export` | GET | none | admin only | anon→401, non-admin→403 |
| `conversations/[sid]/messages` | GET | none | participant or admin | anon→401, non-participant→403 |
| `conversations/[sid]/messages` | POST | impersonation-only | participant or admin; impersonation blocked; author pinned to caller | anon→401, non-participant→403, impersonating-participant→403, author-spoof→403 |
| `conversations/[sid]/media` | POST | impersonation-only | participant or admin; impersonation blocked; author pinned to caller | anon→401, non-participant→403 |
| `conversations/[sid]/messages/[mid]/media/[md]` | GET | none | participant or admin | anon→401, non-participant→403 |
| `conversations/[sid]/close` | POST | none | admin only | anon→401, non-admin→403 |
| `conversations/[sid]/participants` | GET | none | participant or admin | anon→401, non-participant→403 |
| `conversations/[sid]/participants` | POST | none | admin only | anon→401, non-admin→403 |
| `conversations/[sid]/participants/sms` | GET | none | admin only | anon→401, non-admin→403 |
| `conversations/[sid]/participants/sms` | POST | none | admin only | anon→401, non-admin→403 |
| `conversations/link` | POST | none | admin only | anon→401, non-admin→403 |
| `conversations/[sid]/call-proxy` | POST | none (410 body) | admin only (still 410) | anon→401, non-admin→403 |
| `flex/config` | GET | none | admin only | anon→401, non-admin→403 |
| `conversations` (listing) | GET | admin via page-redirect wrapper | admin via API wrapper (clean 401/403) | anon→401, non-admin→403 |

Already-secure, left unchanged (not part of the vulnerability): `join` (auth +
participant), `identities` (auth + participant), `read` (auth; writes only the
caller's own read cursor), `user/conversations`, `token`, `support-conversations`,
`vendors/[id]`, `flex/conversation/[sid]`.

## Source tickets

| Ticket | Status |
|---|---|
| 7577855 — Conversations/messaging subroutes exposed | Closed: messages/media/export/close/participants/participants-sms/link/call-proxy all gated. |
| 7577145 — Flex config exposed | Closed: `flex/config` is admin-only. |
| 7577115 — Proxy relationships oracle (`/api/messaging/relationships`) | Already closed: that route no longer exists in the tree. |
| 7426029 — SmsSignupForm SSRF | Verified already mitigated (CMS-only, allowlisted `script.google.com/macros/s/`; client cannot supply the URL). Tracked separately; not in this PR. |

## Notes / deliberate scope decisions

- **Admin-OR-participant**, not participant-only, on routes the admin dev tools call
  on conversations they are not party to (`messages`, `participants` GET,
  `media`/media-bytes). Participant-only would have broken admin tooling.
- **Author pinning**: on `messages`/`media` POST a participant may only author as
  themselves (`author === userId`); admins may post as any identity for dev tooling.
  Prevents a participant attributing a message to the other party.
- **Impersonation** is enforced inline on member-facing mutations (`messages`/`media`
  POST). Admin routes are inherently impersonation-safe: an impersonating admin holds
  the member's session, so the admin role check already rejects them. `withApiAdminAuth`
  was therefore left unchanged (see the invariant comment on `validateAdminUser`).
- **No new auth wrapper.** Member routes follow the established inline `auth()` pattern
  (join/identities/read); only the authorization rule (`resolveConversationAccess`) is
  shared. An earlier draft introduced a `withApiUserAuth` wrapper; it was removed after
  architectural review to keep one auth style across the member surface.
- **Membership logic is duplicated** between `authorization.ts` and the join route's
  inline helpers. Consolidating (join + identities onto the shared helper) is the
  immediate follow-up PR.
- **Out of scope (tracked):** the `token` route should also block impersonation (a
  Conversations token minted while impersonating grants client-SDK powers as the
  member); and CSRF on state-changing route handlers. Both are post-BUL-86 items.
