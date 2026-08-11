#!/usr/bin/env python3
"""Confirm what a Sanity sync actually did, then record the new checkpoint.

Usage:
    verify.py --plan plan.json --dev dev.json --before prod-before.json \
              --after prod-after.json [--checkpoint docs/audits/checkpoints/sanity-content.json]

Checks:
  1. pushed non-asset fields now equal development
  2. pushed asset references resolve in the target (they are NOT expected to equal
     development, because CHURN and UPLOAD deliberately write production's own id)
  3. preserved production fields untouched
  4. no document outside the plan changed
  5. every asset reference in every touched document resolves

Check 5 is the one that catches the failure this whole design exists to prevent:
a reference copied from one dataset into another that has no such asset renders a
broken image, and nothing else in the pipeline would notice.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

ASSET_ID = re.compile(r'^(image|file)-[0-9a-f]{40}')


def h(v):
    # 64 bits, matching compare.py.
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def doc_of(k):
    return k.split('|', 1)[0]


def is_ref(k, v):
    return k.endswith('.asset._ref') or (isinstance(v, str) and ASSET_ID.match(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', required=True)
    ap.add_argument('--dev', required=True)
    ap.add_argument('--before', required=True)
    ap.add_argument('--after', required=True)
    ap.add_argument('--checkpoint')
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    dev, before, after = (json.load(open(x)) for x in (a.dev, a.before, a.after))
    D, B, A = dev['fields'], before['fields'], after['fields']
    ok = True

    push = plan['push']
    ref_keys = [k for k in push if is_ref(k, D.get(k))]
    plain = [k for k in push if k not in ref_keys]

    missed = [k for k in plain if h(A.get(k, '<absent>')) != h(D.get(k, '<absent>'))]
    print(f"1. pushed content fields match development : {len(plain) - len(missed)}/{len(plain)}")
    if missed:
        ok = False
        for k in missed[:15]:
            print(f"     MISMATCH {k}")

    unresolved = [k for k in ref_keys
                  if isinstance(A.get(k), str) and A[k] not in after['assets']]
    print(f"2. pushed asset references resolve         : "
          f"{len(ref_keys) - len(unresolved)}/{len(ref_keys)}")
    if unresolved:
        ok = False
        for k in unresolved:
            print(f"     DANGLING {k} -> {A.get(k)}")

    moved = [k for k in plan.get('preserve', [])
             if h(A.get(k, '<absent>')) != h(B.get(k, '<absent>'))]
    print(f"3. preserved production fields untouched   : "
          f"{len(plan.get('preserve', [])) - len(moved)}/{len(plan.get('preserve', []))}")
    if moved:
        ok = False
        for k in moved[:15]:
            print(f"     OVERWRITTEN {k}")

    touched = {doc_of(k) for k in push}
    changed_docs = {doc_of(k) for k in set(B) | set(A)
                    if h(B.get(k, '<absent>')) != h(A.get(k, '<absent>'))}
    stray = sorted(changed_docs - touched)
    print(f"4. no unplanned document changed           : {'yes' if not stray else 'NO'}")
    if stray:
        ok = False
        print(f"     {stray}")

    dangling = []
    for k, v in A.items():
        if doc_of(k) in touched and is_ref(k, v) and isinstance(v, str) \
                and v not in after['assets']:
            dangling.append(k)
    print(f"5. all references in touched docs resolve  : {'yes' if not dangling else 'NO'}")
    if dangling:
        ok = False
        for k in dangling[:15]:
            print(f"     BROKEN IMAGE {k} -> {A.get(k)}")

    added = sorted(set(after['assets']) - set(before['assets']))
    if added:
        print(f"\n** {len(added)} new file(s) were added to the target dataset **")
        for r in added:
            m = after['assets'][r]
            print(f"   {m.get('filename')}  {m.get('width')}x{m.get('height')}  "
                  f"{m.get('size')} B   {r}")

    remaining = sorted(k for k in set(D) | set(A)
                       if h(D.get(k, '<absent>')) != h(A.get(k, '<absent>')))
    print(f"\nfields still differing between development and the target: {len(remaining)}")
    if remaining:
        docs = {}
        for k in remaining:
            docs[doc_of(k)] = docs.get(doc_of(k), 0) + 1
        for d, n in sorted(docs.items()):
            print(f"  {d}: {n}")
        print("Each should be an accepted divergence. The checkpoint records them as\n"
              "settled, so they will not be proposed again.")

    print(f"\nVERIFY: {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("Do NOT write the checkpoint. A wrong baseline mis-classifies every future run.")
        return 1

    if a.checkpoint:
        cp = {'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'dev_label': dev['meta']['label'], 'prod_label': after['meta']['label'],
              'dev_captured': dev['meta']['captured_at'],
              'prod_captured': after['meta']['captured_at'],
              'counts': {'dev': dev['meta']['counts'], 'prod': after['meta']['counts']},
              # Hash over the UNION of keys with the same '<absent>' sentinel compare.py
              # uses. Storing only present keys makes a key absent on one side look
              # like that side moved on the next run.
              'dev': {k: h(D.get(k, '<absent>')) for k in sorted(set(D) | set(A))},
              'prod': {k: h(A.get(k, '<absent>')) for k in sorted(set(D) | set(A))}}
        with open(a.checkpoint, 'w') as fh:
            json.dump(cp, fh, indent=1)
        print(f"checkpoint written to {a.checkpoint} "
              f"({len(cp['dev'])} dev / {len(cp['prod'])} prod fields)")
        print("Commit it. Without it the next run cannot tell which side changed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
