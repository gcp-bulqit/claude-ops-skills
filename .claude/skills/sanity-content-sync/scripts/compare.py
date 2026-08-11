#!/usr/bin/env python3
"""Classify every difference between two Sanity dataset snapshots.

Usage:
    compare.py --dev dev.json --prod prod.json [--baseline checkpoint.json]
               [--plan plan.json] [--full]

Classification is three-way against the checkpoint, identical in shape to the
Neon skill:

    neither side moved -> SETTLED       accepted divergence, stays quiet
    development only   -> PUSH          safe to send to production
    production only    -> PRESERVE      production edit, never overwritten
    both               -> CONFLICT      stop, show both, human decides
    no baseline        -> UNCLASSIFIED  direction unknown, human decides

On top of that, every asset reference is sub-classified, because a reference is
only meaningful inside the dataset that holds it:

    RESOLVES   development's asset id already exists in production; copy as-is
    CHURN      production holds the same filename and dimensions under another id;
               keep production's reference, copy nothing
    UPLOAD     no production equivalent; the file must be uploaded first

UPLOAD entries are counted in a required callout, because they are the only case
where syncing adds a file to the production dataset.
"""
import argparse
import hashlib
import json
import re
import sys

ASSET_ID = re.compile(r'^(image|file)-[0-9a-f]{40}')


def h(v):
    # 64 bits is ample for equality here and keeps the committed checkpoint small.
    # Must match across compare.py and verify.py.
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def is_asset_ref(key, dv, pv):
    return key.endswith('.asset._ref') or any(
        isinstance(v, str) and ASSET_ID.match(v) for v in (dv, pv))


def classify_asset(dev_ref, dev_assets, prod_assets):
    """RESOLVES / CHURN / UPLOAD for one development asset reference."""
    if not isinstance(dev_ref, str) or not ASSET_ID.match(dev_ref):
        return 'RESOLVES', None
    if dev_ref in prod_assets:
        return 'RESOLVES', None
    meta = dev_assets.get(dev_ref)
    if not meta:
        return 'UPLOAD', None
    for pid, pm in prod_assets.items():
        if (pm.get('filename') == meta.get('filename')
                and pm.get('width') == meta.get('width')
                and pm.get('height') == meta.get('height')):
            return 'CHURN', pid
    return 'UPLOAD', meta


def doc_of(key):
    return key.split('|', 1)[0]


def render(v, width=200):
    if v is None:
        return 'NULL'
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return repr(s if len(s) <= width else s[:width] + '…')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dev', required=True)
    ap.add_argument('--prod', required=True)
    ap.add_argument('--baseline')
    ap.add_argument('--plan', default='plan.json')
    ap.add_argument('--full', action='store_true')
    a = ap.parse_args()

    dev, prod = json.load(open(a.dev)), json.load(open(a.prod))
    base = json.load(open(a.baseline)) if a.baseline else None
    bd, bp = (base['dev'], base['prod']) if base else ({}, {})
    D, P = dev['fields'], prod['fields']
    DA, PA = dev['assets'], prod['assets']
    width = 10_000 if a.full else 200

    absent = h('<absent>')
    buckets = {'PUSH': [], 'PRESERVE': [], 'CONFLICT': [], 'UNCLASSIFIED': [], 'SETTLED': []}
    for k in sorted(set(D) | set(P)):
        d, p = D.get(k, '<absent>'), P.get(k, '<absent>')
        if h(d) == h(p):
            continue
        if base is None:
            buckets['UNCLASSIFIED'].append(k)
            continue
        # A key absent from the checkpoint existed on neither side then, so it must
        # read as the '<absent>' sentinel, not as None. Bare .get() made every field
        # newly created in development look like production had moved too.
        dm, pm = h(d) != bd.get(k, absent), h(p) != bp.get(k, absent)
        if dm and not pm:
            buckets['PUSH'].append(k)
        elif pm and not dm:
            buckets['PRESERVE'].append(k)
        elif dm and pm:
            buckets['CONFLICT'].append(k)
        else:
            buckets['SETTLED'].append(k)

    assets = {}
    for k in buckets['PUSH'] + buckets['CONFLICT'] + buckets['UNCLASSIFIED']:
        if is_asset_ref(k, D.get(k), P.get(k)):
            verdict, detail = classify_asset(D.get(k), DA, PA)
            assets[k] = {'verdict': verdict, 'prod_equivalent':
                         detail if isinstance(detail, str) else None}

    uploads = [k for k, v in assets.items() if v['verdict'] == 'UPLOAD']
    churn = [k for k, v in assets.items() if v['verdict'] == 'CHURN']
    resolves = [k for k, v in assets.items() if v['verdict'] == 'RESOLVES']

    docs_only_dev = sorted({doc_of(k) for k in set(D) - set(P)})
    docs_only_prod = sorted({doc_of(k) for k in set(P) - set(D)})

    differing = sum(len(v) for k, v in buckets.items() if k != 'SETTLED')
    gate = (base is not None and not buckets['PRESERVE'] and not buckets['CONFLICT']
            and not buckets['UNCLASSIFIED'])

    # Written BEFORE the report. The report is thousands of lines, so piping this
    # command through head/less kills it with SIGPIPE partway through printing.
    # Writing the plan last meant the truncated run left the PREVIOUS run's plan on
    # disk while the summary on screen described the new one, and the next step
    # built mutations from the stale file.
    plan = {'path': 'sync' if differing == 0 else ('A' if gate else 'B'),
            'push': buckets['PUSH'], 'preserve': buckets['PRESERVE'],
            'conflict': buckets['CONFLICT'], 'unclassified': buckets['UNCLASSIFIED'],
            'settled': buckets['SETTLED'], 'assets': assets, 'uploads': uploads,
            'dev': a.dev, 'prod': a.prod, 'baseline': a.baseline}
    with open(a.plan, 'w') as fh:
        json.dump(plan, fh, indent=1)

    print("=" * 78)
    print(f"development : {dev['meta']['label']}  captured {dev['meta']['captured_at']}")
    print(f"production  : {prod['meta']['label']}  captured {prod['meta']['captured_at']}")
    print(f"baseline    : {base['recorded_at'] if base else 'NONE — every difference is UNCLASSIFIED'}")
    print("=" * 78)
    print(f"  content docs  dev {dev['meta']['counts']['content']:4}   "
          f"prod {prod['meta']['counts']['content']:4}")
    print(f"  assets        dev {dev['meta']['counts']['asset']:4}   "
          f"prod {prod['meta']['counts']['asset']:4}")

    path = ('IN SYNC — no field differs. Nothing to push.' if differing == 0
            else 'A (fast-forward, development-only changes)' if gate
            else 'B (merge required)')
    print(f"\nplan written to {a.plan}")
    print(f"PATH: {path}")
    print(f"  push {len(buckets['PUSH'])}   preserve {len(buckets['PRESERVE'])}   "
          f"conflict {len(buckets['CONFLICT'])}   unclassified {len(buckets['UNCLASSIFIED'])}   "
          f"settled {len(buckets['SETTLED'])}")

    print("\n" + "-" * 78)
    if uploads:
        print(f"** {len(uploads)} NEW FILE(S) WILL BE ADDED TO THE PRODUCTION DATASET **")
        for k in uploads:
            m = DA.get(D.get(k), {})
            print(f"   {k}")
            print(f"     {m.get('filename')}  {m.get('width')}x{m.get('height')}  "
                  f"{m.get('size')} B  {m.get('mime')}")
        print("   Uploading changes the asset id: the CDN does not return the bytes that")
        print("   were originally uploaded, so production gets a different id than")
        print("   development and the two datasets diverge permanently on this reference.")
    else:
        print("No new files will be added to the production dataset.")
    if churn:
        print(f"\n{len(churn)} asset reference(s) are the same artwork under a different id.")
        print("Production's reference is kept; nothing is copied and nothing changes visually.")
        for k in churn:
            m = DA.get(D.get(k), {})
            print(f"   {k}  ({m.get('filename')} {m.get('width')}x{m.get('height')})")
    if resolves:
        print(f"\n{len(resolves)} asset reference(s) already resolve in production and copy as-is.")
    print("-" * 78)

    def section(title, keys, both):
        if not keys:
            return
        print("\n" + "=" * 78)
        print(f"{title}  ({len(keys)})")
        print("=" * 78)
        for k in keys:
            doc = doc_of(k)
            note = ''
            if k in assets:
                note = f"   [asset: {assets[k]['verdict']}]"
                if assets[k]['prod_equivalent']:
                    note += f" keep {assets[k]['prod_equivalent'][:38]}…"
            print(f"\n{k}{note}")
            entries = [('development', D.get(k, '<absent>'),
                        dev['display'].get(doc, {}).get('updatedAt') or ''),
                       ('production', P.get(k, '<absent>'),
                        prod['display'].get(doc, {}).get('updatedAt') or '')]
            if both:
                entries.sort(key=lambda e: e[2], reverse=True)
                for i, (side, val, ts) in enumerate(entries, 1):
                    print(f"  {i}. {side:12} document last updated {ts or 'unknown'}")
                    print(f"     {render(val, width)}")
            else:
                print(f"     PROD (current) : {render(P.get(k, '<absent>'), width)}")
                print(f"     DEV  (proposed): {render(D.get(k, '<absent>'), width)}")

    section("CONFLICT — both sides changed this since the last sync. Your call.",
            buckets['CONFLICT'], True)
    section("UNCLASSIFIED — no baseline, direction unknown. Your call.",
            buckets['UNCLASSIFIED'], True)
    section("PRESERVE — production changed these, development did not. NOT pushed.",
            buckets['PRESERVE'], True)
    section("PUSH — development changed these, production did not.", buckets['PUSH'], False)

    if buckets['SETTLED']:
        print("\n" + "=" * 78)
        print(f"SETTLED ({len(buckets['SETTLED'])}) — differ, but both sides match the last "
              "agreed state. No action.")
        for k in buckets['SETTLED']:
            print(f"  {k}")

    if docs_only_dev or docs_only_prod:
        print(f"\ndocuments with fields only on one side:")
        print(f"  development: {docs_only_dev}")
        print(f"  production : {docs_only_prod}")

    print(f"\nplan written to {a.plan}")
    print("Nothing has been written to either dataset.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
