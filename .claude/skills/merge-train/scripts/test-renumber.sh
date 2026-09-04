#!/usr/bin/env bash
# Exercise renumber-migration.mjs against synthetic drizzle conflicts.
set -uo pipefail
S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/renumber-migration.mjs"
ROOT="${TMPDIR:-/tmp}/merge-train-tests"
M=packages/database/migrations
PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1"; echo "        want: $3"; echo "        got:  $2"; fi; }

tags() { python3 -c "
import json;print(','.join('%d:%s'%(e['idx'],e['tag']) for e in json.load(open('$M/meta/_journal.json'))['entries']))"; }
whens() { python3 -c "
import json;print(','.join(str(e['when']) for e in json.load(open('$M/meta/_journal.json'))['entries']))"; }
addmig() { # tag when
  echo "-- $1" > "$M/$1.sql"; echo "{\"s\":\"$1\"}" > "$M/meta/${1:0:4}_snapshot.json"
  python3 -c "
import json,sys
p='$M/meta/_journal.json'; j=json.load(open(p))
j['entries'].append({'idx':int('$1'[:4]),'version':'7','when':$2,'tag':'$1','breakpoints':True})
json.dump(j,open(p,'w'),indent=2)"
}
newrepo() {
  rm -rf "$ROOT/$1"; mkdir -p "$ROOT/$1/$M/meta"; cd "$ROOT/$1" || exit 1
  git init -q -b development; git config user.email t@t.t; git config user.name t
  printf '{\n  "version": "7",\n  "dialect": "postgresql",\n  "entries": []\n}\n' > "$M/meta/_journal.json"
  addmig 0000_init 1700000000000
  git add -A; git commit -qm base
}

echo "== 1. conflicted merge: ours and theirs both claim 0001 =="
newrepo t1
addmig 0001_theirs 1700000001000; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~1
addmig 0001_ours 1700000000500; git add -A; git commit -qm ours
git merge development >/dev/null 2>&1
node "$S" >/dev/null 2>&1
check "renumbered onto the end"   "$(tags)" "0:0000_init,1:0001_theirs,2:0002_ours"
check "our sql body survived"     "$(cat $M/0002_ours.sql)" "-- 0001_ours"
check "our snapshot survived"     "$(cat $M/meta/0002_snapshot.json)" '{"s":"0001_ours"}'
check "their snapshot intact"     "$(cat $M/meta/0001_snapshot.json)" '{"s":"0001_theirs"}'
check "no unmerged paths left"    "$(git diff --name-only --diff-filter=U)" ""
check "stale 0001_ours.sql gone"  "$([ -e $M/0001_ours.sql ] && echo yes || echo no)" "no"
W=$(whens | cut -d, -f3); [ "$W" -gt 1700000001000 ] && ok "when cleared their high-water mark" || bad "when=$W not > 1700000001000"

echo "== 2. rerun after committing the merge is a no-op =="
git commit -qm renumber >/dev/null 2>&1
BEFORE_T=$(tags); BEFORE_W=$(whens)
OUT=$(node "$S" 2>&1)
check "reports nothing to do"     "$OUT" "renumber-migration: numbering already correct, nothing to do"
check "tags unchanged"            "$(tags)" "$BEFORE_T"
check "whens unchanged"           "$(whens)" "$BEFORE_W"
check "tree clean"                "$(git status --porcelain)" ""

echo "== 3. index is already free, but our 'when' predates theirs =="
newrepo t3
addmig 0001_theirs 1700000009000; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~1
addmig 0002_ours 1700000000500; git add -A; git commit -qm ours   # idx 2 already free
git merge development >/dev/null 2>&1; node "$S" >/dev/null 2>&1
check "index left alone"          "$(tags)" "0:0000_init,1:0001_theirs,2:0002_ours"
W=$(whens | cut -d, -f3); [ "$W" -gt 1700000009000 ] && ok "stale when restamped anyway" || bad "when=$W not > 1700000009000"

echo "== 4. refuses when our branch edited an existing migration =="
newrepo t4
addmig 0001_theirs 1700000001000; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~1
echo "-- tampered" >> $M/0000_init.sql; addmig 0001_ours 1700000000500
git add -A; git commit -qm ours
git merge development >/dev/null 2>&1
OUT=$(node "$S" 2>&1); RC=$?
check "exits non-zero"            "$RC" "1"
[[ "$OUT" == *"did not add"* ]] && ok "explains why it refused" || bad "unclear refusal: $OUT"

echo "== 5. conflict outside the migrations dir is refused =="
newrepo t5
echo one > app.txt; git add -A; git commit -qm app
addmig 0001_theirs 1700000001000; echo theirs > app.txt; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~2
addmig 0001_ours 1700000000500; echo ours > app.txt; git add -A; git commit -qm ours
git merge development >/dev/null 2>&1
OUT=$(node "$S" 2>&1); RC=$?
check "exits non-zero"            "$RC" "1"
[[ "$OUT" == *"outside"* ]] && ok "names the outside conflict" || bad "unclear refusal: $OUT"

echo "== 6. PR adding several migrations keeps their relative order =="
newrepo t6
addmig 0001_theirs 1700000001000; addmig 0002_theirs2 1700000002000; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~1
addmig 0001_ours_a 1700000000400; addmig 0002_ours_b 1700000000500; addmig 0003_ours_c 1700000000600
git add -A; git commit -qm ours
git merge development >/dev/null 2>&1; node "$S" >/dev/null 2>&1
check "contiguous, order preserved" "$(tags)" "0:0000_init,1:0001_theirs,2:0002_theirs2,3:0003_ours_a,4:0004_ours_b,5:0005_ours_c"
W=$(whens); A=$(echo $W|cut -d, -f4); B=$(echo $W|cut -d, -f5); C=$(echo $W|cut -d, -f6)
{ [ "$A" -gt 1700000002000 ] && [ "$B" -gt "$A" ] && [ "$C" -gt "$B" ]; } && ok "whens strictly ascending past theirs" || bad "whens: $A $B $C"
check "bodies follow their tags" "$(cat $M/0004_ours_b.sql)" "-- 0002_ours_b"

echo "== 7. migration with no snapshot file (86 of these exist upstream) =="
newrepo t7
addmig 0001_theirs 1700000001000; git add -A; git commit -qm theirs
git checkout -q -b feature HEAD~1
addmig 0001_ours 1700000000500; rm $M/meta/0001_snapshot.json   # hand-numbered data migration
git add -A; git commit -qm ours
git merge development >/dev/null 2>&1
OUT=$(node "$S" 2>&1); RC=$?
check "handled without error" "$RC" "0"
check "renumbered anyway" "$(tags)" "0:0000_init,1:0001_theirs,2:0002_ours"
check "no phantom snapshot created" "$([ -e $M/meta/0002_snapshot.json ] && echo yes || echo no)" "no"
check "their snapshot untouched" "$(cat $M/meta/0001_snapshot.json)" '{"s":"0001_theirs"}'

echo; echo "passed=$PASS failed=$FAIL"; [ "$FAIL" -eq 0 ]
