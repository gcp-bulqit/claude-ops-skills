#!/usr/bin/env python3
"""Split the Linear issues a promotion's new PRs mention into delivered and referenced.

A PR title's bracketed tickets are what that PR delivers. A ticket that appears
only in the body is usually the opposite: work explicitly deferred, tracked
separately, or frozen in a backlog. Sweeping the second set to "Live - Final
Testing" marks work live that has not shipped.

The split is mechanical; the verdict is not. This prints the body-only refs with
their surrounding sentence so each can be judged before anything is written.

  python3 linear_refs.py 1016 1015 1011 ...
"""
import argparse
import json
import re
import subprocess
import sys

REF = re.compile(r'\b(BUL-\d+)\b')


def gh(cmd):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip())
    return out.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prs', nargs='+', type=int)
    a = ap.parse_args()

    delivered, referenced, per_pr = {}, {}, {}
    for pr in a.prs:
        d = json.loads(gh(f'gh pr view {pr} --json title,body'))
        title, body = d['title'], d['body'] or ''
        t_refs = REF.findall(title)
        b_refs = [r for r in REF.findall(body) if r not in t_refs]
        per_pr[pr] = {'title': title, 'delivered': t_refs}
        for r in t_refs:
            delivered.setdefault(r, []).append(pr)
        for r in dict.fromkeys(b_refs):
            for line in body.splitlines():
                if r in line:
                    referenced.setdefault(r, []).append(
                        {'pr': pr, 'context': ' '.join(line.split())[:240]})
                    break

    # A body-only ref elsewhere is fine if some other PR delivers it.
    body_only = {r: v for r, v in referenced.items() if r not in delivered}

    print('=== DELIVERED (title brackets) -- these are the sweep set ===')
    for r in sorted(delivered, key=lambda x: int(x.split('-')[1])):
        print(f"  {r:10s} <- PR {', '.join('#'+str(p) for p in delivered[r])}")
    print(f"\n  {len(delivered)} issues across {len(a.prs)} PRs")

    print('\n=== BODY-ONLY (judge each; usually deferred, do NOT sweep blind) ===')
    for r in sorted(body_only, key=lambda x: int(x.split('-')[1])):
        for c in body_only[r]:
            print(f"  {r:10s} #{c['pr']}: {c['context']}")
    if not body_only:
        print('  none')

    print('\n=== per PR ===')
    for pr, d in per_pr.items():
        print(f"  #{pr}: {', '.join(d['delivered']) or '(no ticket in title)'}  {d['title'][:70]}")

    json.dump({'delivered': delivered, 'body_only': body_only},
              open('/dev/stderr', 'w'), indent=2)


if __name__ == '__main__':
    main()
