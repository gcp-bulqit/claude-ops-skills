#!/usr/bin/env python3
"""Classify every difference between two notification snapshots.

Usage:
    compare.py --dev dev.json --prod prod.json [--baseline checkpoint.json]
               [--plan plan.json] [--full]

Without a baseline the skill cannot tell "development changed this" from
"production changed this", only that they differ. Every difference is then
UNCLASSIFIED and must be decided by a human. With a baseline, each side is
compared against its own recorded state at the last agreed sync:

    dev changed?   D[k] != baseline.dev[k]
    prod changed?  P[k] != baseline.prod[k]

    neither  -> SETTLED       a divergence both sides already agreed to keep
    dev only -> PUSH          safe to send to production
    prod only-> PRESERVE      production edit development has not received
    both     -> CONFLICT      stop, show both values

SETTLED is why the baseline records both sides rather than one merged state. A
deviation deliberately kept on one side stays quiet instead of resurfacing as a
proposed change on every future run.
"""
import argparse
import hashlib
import json
import sys


def h(v):
    # 64 bits is ample for equality over a few thousand fields, and keeps the
    # committed checkpoint small. Must match across compare.py and verify.py.
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def owners(snap, key):
    """Template codes that a field key belongs to, for display."""
    kind, _, rest = key.partition(':')
    if kind in ('template', 'link'):
        return [rest.split(':')[0]]
    if kind in ('email', 'alert', 'inbox'):
        return snap.get('owners', {}).get(f"{kind}:{rest.split(':')[0]}", [])
    return []


def stamps(snap, code):
    d = snap.get('display', {}).get(code, {})
    tid = d.get('template_id')
    hist = snap.get('latest_history', {}).get(str(tid), {})
    return {'updated_at': d.get('updated_at'), 'admin': d.get('updated_by_admin_id'),
            'last_history': hist.get('created_at'), 'history_admin': hist.get('admin_id')}


def render(v, width=180):
    if v is None:
        return 'NULL'
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
        n = len(v) if isinstance(v, list) else len(v.keys())
        head = f"<{type(v).__name__}, {n} entries> "
        return head + (s if len(s) <= width else s[:width] + '…')
    s = str(v)
    return repr(s if len(s) <= width else s[:width] + '…')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dev', required=True)
    ap.add_argument('--prod', required=True)
    ap.add_argument('--baseline')
    ap.add_argument('--plan', default='plan.json')
    ap.add_argument('--full', action='store_true', help='render full values, no truncation')
    a = ap.parse_args()

    dev = json.load(open(a.dev))
    prod = json.load(open(a.prod))
    base = json.load(open(a.baseline)) if a.baseline else None
    bd = base['dev'] if base else {}
    bp = base['prod'] if base else {}

    D, P = dev['fields'], prod['fields']
    keys = sorted(set(D) | set(P))
    buckets = {'PUSH': [], 'PRESERVE': [], 'CONFLICT': [], 'UNCLASSIFIED': [], 'SETTLED': []}

    for k in keys:
        d, p = D.get(k, '<absent>'), P.get(k, '<absent>')
        if h(d) == h(p):
            continue
        if base is None:
            buckets['UNCLASSIFIED'].append(k)
            continue
        dev_moved = h(d) != bd.get(k)
        prod_moved = h(p) != bp.get(k)
        if dev_moved and not prod_moved:
            buckets['PUSH'].append(k)
        elif prod_moved and not dev_moved:
            buckets['PRESERVE'].append(k)
        elif dev_moved and prod_moved:
            buckets['CONFLICT'].append(k)
        else:
            buckets['SETTLED'].append(k)

    # structural checks that are never auto-pushed
    link_issues = [k for k in buckets['PUSH'] + buckets['CONFLICT'] + buckets['UNCLASSIFIED']
                   if k.startswith('link:')]
    only_dev = sorted(set(D) - set(P))
    only_prod = sorted(set(P) - set(D))

    width = 10_000 if a.full else 180
    print("=" * 78)
    print(f"development : {dev['meta']['label']}  captured {dev['meta']['captured_at']}")
    print(f"production  : {prod['meta']['label']}  captured {prod['meta']['captured_at']}")
    print(f"baseline    : {base['recorded_at'] if base else 'NONE — every difference is UNCLASSIFIED'}")
    print("=" * 78)
    for name, label in (('templates', 'templates'), ('emails', 'email rows'),
                        ('alerts', 'alert rows'), ('inbox', 'inbox rows'),
                        ('cancellations', 'cancellations')):
        dc, pc = dev['meta']['counts'][name], prod['meta']['counts'][name]
        flag = '' if dc == pc else '   <-- ROW COUNT MISMATCH'
        print(f"  {label:16} dev {dc:4}   prod {pc:4}{flag}")
    if only_dev or only_prod:
        print(f"\n  fields only in development: {len(only_dev)}")
        print(f"  fields only in production : {len(only_prod)}")
    if link_issues:
        print(f"\n  ** LINKAGE DIFFERS on {len(link_issues)} field(s): {link_issues}")
        print("     email_id / dashboard_alert_id wiring is not the same on both sides.")
        print("     Never auto-push these. Resolve by hand before syncing content.")

    differing = sum(len(v) for k, v in buckets.items() if k != 'SETTLED')
    gate = (not buckets['PRESERVE'] and not buckets['CONFLICT']
            and not buckets['UNCLASSIFIED'] and not link_issues and base is not None)
    if differing == 0:
        path = 'IN SYNC — no field differs. Nothing to push.'
    elif gate:
        path = 'A (fast-forward, development-only changes)'
    else:
        path = 'B (merge required)'
    print(f"\nPATH: {path}")
    print(f"  push {len(buckets['PUSH'])}   preserve {len(buckets['PRESERVE'])}   "
          f"conflict {len(buckets['CONFLICT'])}   unclassified {len(buckets['UNCLASSIFIED'])}   "
          f"settled {len(buckets['SETTLED'])}")

    def section(title, keys, show_both):
        if not keys:
            return
        print("\n" + "=" * 78)
        print(f"{title}  ({len(keys)})")
        print("=" * 78)
        for k in keys:
            codes = owners(dev, k) or owners(prod, k)
            ds = [stamps(dev, c) for c in codes]
            ps = [stamps(prod, c) for c in codes]
            tag = f"  [{', '.join(codes)}]" if codes else ""
            if len(codes) > 1:
                tag += "   ** SHARED ROW: this write affects every code listed **"
            print(f"\n{k}{tag}")
            entries = [
                ('development', D.get(k, '<absent>'),
                 max([x['last_history'] or x['updated_at'] or '' for x in ds], default='')),
                ('production', P.get(k, '<absent>'),
                 max([x['last_history'] or x['updated_at'] or '' for x in ps], default='')),
            ]
            if show_both:
                entries.sort(key=lambda e: e[2] or '', reverse=True)
                for i, (side, val, ts) in enumerate(entries, 1):
                    print(f"  {i}. {side:12} last edit {ts or 'unknown'}")
                    print(f"     {render(val, width)}")
            else:
                print(f"     PROD (current): {render(P.get(k, '<absent>'), width)}")
                print(f"     DEV  (proposed): {render(D.get(k, '<absent>'), width)}")

    section("CONFLICT — both sides changed this since the last sync. Your call.",
            buckets['CONFLICT'], True)
    section("UNCLASSIFIED — no baseline, direction unknown. Your call.",
            buckets['UNCLASSIFIED'], True)
    section("PRESERVE — production changed these, development did not. NOT pushed.",
            buckets['PRESERVE'], True)
    section("PUSH — development changed these, production did not.",
            buckets['PUSH'], False)

    if buckets['SETTLED']:
        print("\n" + "=" * 78)
        print(f"SETTLED  ({len(buckets['SETTLED'])}) — differ, but both sides match the last "
              f"agreed state.\nThese are accepted divergences. No action.")
        for k in buckets['SETTLED']:
            print(f"  {k}")

    plan = {'path': 'sync' if differing == 0 else ('A' if gate else 'B'), 'push': buckets['PUSH'],
            'preserve': buckets['PRESERVE'], 'conflict': buckets['CONFLICT'],
            'unclassified': buckets['UNCLASSIFIED'], 'settled': buckets['SETTLED'],
            'link_issues': link_issues, 'dev': a.dev, 'prod': a.prod,
            'baseline': a.baseline}
    with open(a.plan, 'w') as fh:
        json.dump(plan, fh, indent=1)
    print(f"\nplan written to {a.plan}")
    print("Nothing has been written to any database.")
    if differing and not gate:
        print("Path B: decide each CONFLICT / UNCLASSIFIED entry, then add the chosen keys")
        print("to plan['push'] (or leave them out) before running plan_to_sql.py.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
