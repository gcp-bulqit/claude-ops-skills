#!/usr/bin/env python3
"""Confirm what a sync actually did, then record the new checkpoint.

Usage:
    verify.py --plan plan.json --dev dev.json --before prod-before.json \
              --after prod-after.json [--checkpoint docs/audits/checkpoints/neon-notifications.json]

Checks, in order:
  1. every pushed key now equals development on the target
  2. every PRESERVE key is untouched from its pre-sync value
  3. no key outside the plan changed on the target
  4. row counts are unchanged
  5. bookkeeping columns are unchanged (a content sync writes none)

Only when all five pass does it offer to write the checkpoint. The checkpoint
records a hash per field for BOTH sides, so a divergence deliberately left in
place reads as "neither side changed" on the next run instead of resurfacing.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

BOOK = ('created_at', 'updated_at', 'created_by_admin_id', 'updated_by_admin_id')


def h(v):
    # 64 bits is ample for equality over a few thousand fields, and keeps the
    # committed checkpoint small. Must match across compare.py and verify.py.
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', required=True)
    ap.add_argument('--dev', required=True)
    ap.add_argument('--before', required=True)
    ap.add_argument('--after', required=True)
    ap.add_argument('--checkpoint')
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    dev = json.load(open(a.dev))
    before = json.load(open(a.before))
    after = json.load(open(a.after))
    D, B, A = dev['fields'], before['fields'], after['fields']
    ok = True

    missed = [k for k in plan['push'] if h(A.get(k, '<absent>')) != h(D.get(k, '<absent>'))]
    print(f"1. pushed fields now match development : {len(plan['push']) - len(missed)}/{len(plan['push'])}")
    if missed:
        ok = False
        for k in missed:
            print(f"     MISMATCH {k}")

    moved = [k for k in plan.get('preserve', []) if h(A.get(k, '<absent>')) != h(B.get(k, '<absent>'))]
    print(f"2. preserved production fields untouched: {len(plan.get('preserve', [])) - len(moved)}"
          f"/{len(plan.get('preserve', []))}")
    if moved:
        ok = False
        for k in moved:
            print(f"     OVERWRITTEN {k}")

    planned = set(plan['push'])
    stray = [k for k in set(B) | set(A)
             if k not in planned and h(B.get(k, '<absent>')) != h(A.get(k, '<absent>'))]
    print(f"3. no unplanned field changed          : {'yes' if not stray else 'NO'}")
    if stray:
        ok = False
        for k in stray[:20]:
            print(f"     UNPLANNED {k}")

    same_counts = before['meta']['counts'] == after['meta']['counts']
    print(f"4. row counts unchanged                : {'yes' if same_counts else 'NO'}")
    if not same_counts:
        ok = False
        print(f"     before {before['meta']['counts']}\n     after  {after['meta']['counts']}")

    book_moved = []
    for code, d in before.get('display', {}).items():
        after_d = after.get('display', {}).get(code, {})
        for col in BOOK:
            if str(d.get(col)) != str(after_d.get(col)):
                book_moved.append(f"{code}.{col}")
    print(f"5. bookkeeping columns unchanged       : {'yes' if not book_moved else 'NO'}")
    if book_moved:
        ok = False
        print(f"     {book_moved[:10]}")

    remaining = sorted(k for k in set(D) | set(A)
                       if h(D.get(k, '<absent>')) != h(A.get(k, '<absent>')))
    print(f"\nfields still differing between development and the target: {len(remaining)}")
    for k in remaining:
        print(f"  {k}")
    if remaining:
        print("Each of these should be an accepted divergence. The checkpoint records them\n"
              "as settled, so they will not be proposed again.")

    print(f"\nVERIFY: {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("Do NOT write the checkpoint. Investigate first; a wrong baseline\n"
              "mis-classifies every future run.")
        return 1

    if a.checkpoint:
        cp = {'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'dev_label': dev['meta']['label'], 'prod_label': after['meta']['label'],
              'dev_captured': dev['meta']['captured_at'],
              'prod_captured': after['meta']['captured_at'],
              'counts': after['meta']['counts'],
              # Hash over the UNION of keys with the same '<absent>' sentinel compare.py
              # uses. Storing only present keys makes a key absent on one side look
              # like that side moved on the next run.
              'dev': {k: h(D.get(k, '<absent>')) for k in sorted(set(D) | set(A))},
              'prod': {k: h(A.get(k, '<absent>')) for k in sorted(set(D) | set(A))}}
        with open(a.checkpoint, 'w') as fh:
            json.dump(cp, fh, indent=1)
        print(f"checkpoint written to {a.checkpoint} ({len(cp['dev'])} dev / {len(cp['prod'])} prod fields)")
        print("Commit it. Without it the next run cannot tell which side changed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
