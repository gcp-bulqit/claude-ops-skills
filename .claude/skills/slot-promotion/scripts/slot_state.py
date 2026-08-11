#!/usr/bin/env python3
"""Derive everything about a promotion PR that can be read from git and the platforms.

Input is a PR number. The target slot comes from the PR's base branch, never from
an argument, so the blue/green distinction cannot be typed in wrong.

Emits JSON on stdout. Nothing here writes.
"""
import argparse
import json
import re
import subprocess
import sys

SLOTS = ('blue', 'green')
SOURCE = 'development'
PR_RE = re.compile(r'\(#(\d+)\)\s*$')


def run(cmd, check=True):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and out.returncode != 0:
        sys.exit(f"failed: {cmd}\n{out.stderr.strip()}")
    return out.stdout.strip()


def commits(rng):
    """Commits in a range as [{sha, subject, pr, is_merge}]."""
    raw = run(f"git log {rng} --format='%h|%p|%s'")
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, parents, subject = line.split('|', 2)
        m = PR_RE.search(subject)
        rows.append({
            'sha': sha,
            'subject': subject,
            'pr': int(m.group(1)) if m else None,
            'is_merge': len(parents.split()) > 1,
        })
    return rows


def serving_slot():
    """Which slot currently holds the production apex domain.

    Read from the newest production deployment's aliases. `head_branch` on a
    workflow run and the branch name on a deployment both lie about this often
    enough that the alias list is the only trustworthy source.
    """
    ls = run('timeout 120 npx vercel@53.4.0 ls --prod 2>/dev/null', check=False)
    url = None
    for line in ls.splitlines():
        m = re.search(r'(https://bul-website-[a-z0-9]+-bulqit\.vercel\.app)', line)
        if m:
            url = m.group(1)
            break
    if not url:
        return {'slot': None, 'reason': 'could not list production deployments'}

    info = run(f'timeout 120 npx vercel@53.4.0 inspect {url} 2>&1', check=False)
    apex = 'bulqit.com' in info
    for slot in SLOTS:
        if f'bul-website-git-{slot}-bulqit' in info:
            return {'slot': slot, 'deployment': url, 'holds_apex': apex}
    return {'slot': None, 'deployment': url, 'reason': 'no slot alias on the deployment'}


def last_worker_deploy():
    """Environment of the most recent Deploy Workers run that targeted production.

    The `environment` input is the only thing that decides whether a run reaches
    the unsuffixed production Fly apps. It is NOT visible on the run object:
    `head_branch` reads `development` on every dispatch regardless of target, so
    the run log has to be read.
    """
    ids = run("gh run list --workflow=deploy-workers.yml --limit 8 "
              "--json databaseId -q '.[].databaseId'", check=False)
    for rid in ids.splitlines():
        rid = rid.strip()
        if not rid:
            continue
        created = run(f"gh api repos/{{owner}}/{{repo}}/actions/runs/{rid} -q .created_at", check=False)
        log = run(f"timeout 240 gh run view {rid} --log 2>/dev/null | grep -m1 'ENVIRONMENT:'", check=False)
        env = log.split('ENVIRONMENT:')[-1].strip() if 'ENVIRONMENT:' in log else None
        if env in SLOTS:
            return {'run_id': rid, 'created_at': created, 'environment': env, 'targets': 'production apps'}
    return {'environment': None, 'reason': 'no slot-targeted run in the last 8'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pr', type=int)
    ap.add_argument('--skip-platform', action='store_true',
                    help='skip Vercel and Actions lookups (offline analysis only)')
    a = ap.parse_args()

    meta = json.loads(run(
        f"gh pr view {a.pr} --json number,title,baseRefName,headRefName,state,commits,mergeStateStatus"))
    target = meta['baseRefName']
    head = meta['headRefName']

    if target not in SLOTS:
        sys.exit(f"PR #{a.pr} targets '{target}', which is not a deploy slot. "
                 f"This skill only handles promotions into {' or '.join(SLOTS)}.")
    if head != SOURCE:
        sys.exit(f"PR #{a.pr} head is '{head}', not '{SOURCE}'. A slot takes code only "
                 f"as a promotion of {SOURCE}; never point a feature branch at a slot.")

    other = SLOTS[0] if target == SLOTS[1] else SLOTS[1]
    run(f'git fetch origin {SOURCE} {" ".join(SLOTS)} --quiet', check=False)

    # A merged PR's branch tips have moved past it, so the live ranges no longer
    # describe what it carried. Reconstruct from the merge commit: first parent is
    # the slot immediately before the merge, second is development as merged, and
    # the other slot is taken as of the merge timestamp.
    merged = meta['state'] == 'MERGED'
    if merged:
        oid = json.loads(run(f'gh pr view {a.pr} --json mergeCommit'))['mergeCommit']['oid']
        target_ref = f'{oid}^1'
        source_ref = f'{oid}^2'
        when = run(f'git log -1 --format=%cI {oid}')
        # --first-parent is load-bearing. A slot advances only by promotion merges, so
        # its first-parent chain IS the sequence of its own tips. Without it, rev-list
        # walks into the development commits the slot has since absorbed and returns
        # one of those, which silently collapses the two carried groups into one.
        other_ref = (run(f'git rev-list -1 --first-parent --before="{when}" origin/{other}')
                     or f'origin/{other}')
        basis = {'mode': 'historical', 'merge_commit': oid, 'merged_at': when,
                 'note': 'ranges reconstructed from the merge commit, not from current tips'}
    else:
        target_ref, source_ref, other_ref = f'origin/{target}', f'origin/{SOURCE}', f'origin/{other}'
        basis = {'mode': 'live'}

    already = commits(f'{target_ref}..{other_ref}')
    new = commits(f'{other_ref}..{source_ref}')
    unique_to_target = commits(f'{source_ref}..{target_ref}')

    already_prs = [c for c in already if not c['is_merge']]
    new_prs = [c for c in new if not c['is_merge']]

    merge_tree = run(f'git merge-tree --write-tree {target_ref} {source_ref}', check=False)
    dev_tree = run(f'git rev-parse {source_ref}^{{tree}}')

    result = {
        'pr': meta['number'],
        'pr_title': meta['title'],
        'pr_state': meta['state'],
        'merge_state': meta.get('mergeStateStatus'),
        'target_slot': target,
        'other_slot': other,
        'source': SOURCE,
        'basis': basis,
        'tips': {b: run(f"git log -1 --format='%h %ci %s' origin/{b}") for b in (SOURCE,) + SLOTS},
        'source_tip_sha': run(f'git rev-parse --short {source_ref}'),
        'already_serving_on_other': already_prs,
        'new_to_both': new_prs,
        'counts': {
            'pr_commits': len(meta['commits']),
            'already_serving': len(already_prs),
            'new_to_both': len(new_prs),
            'sum': len(already_prs) + len(new_prs),
            'reconciles': len(already_prs) + len(new_prs) == len(meta['commits']),
        },
        'target_carries_only_merge_points': all(c['is_merge'] for c in unique_to_target),
        'commits_unique_to_target': unique_to_target,
        'merge_tree': merge_tree,
        'source_tree': dev_tree,
        'tree_identical': merge_tree == dev_tree,
    }

    if not result['counts']['reconciles']:
        result['WARN'] = (
            f"the two carried groups sum to {result['counts']['sum']} but the PR has "
            f"{result['counts']['pr_commits']} commits. Do not paper over this: it means the "
            f"ranges do not describe what the PR carries. Check whether the PR merged while "
            f"you were working, or whether a slot has taken code from somewhere other than a "
            f"promotion.")

    if not a.skip_platform:
        result['serving'] = serving_slot()
        result['last_production_worker_deploy'] = last_worker_deploy()
        s = result['serving'].get('slot')
        result['targets_standby'] = (s is not None and s != target)
        # Only meaningful while the PR is open. After the merge the promoted slot is
        # expected to become the serving one, so the same comparison would misfire.
        if s == target and not merged:
            result['REFUSE'] = (
                f"'{target}' is the slot currently serving production. Promoting into it "
                f"changes what production is serving right now. Promote into '{other}' "
                f"instead, or cut traffic first.")

    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
