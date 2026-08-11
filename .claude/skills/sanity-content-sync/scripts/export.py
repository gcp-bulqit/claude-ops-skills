#!/usr/bin/env python3
"""Export a Sanity dataset and flatten it into comparable leaf fields.

Usage:
    export.py <dataset> <out.json> [--project 5z88vhq0] [--token-env SANITY_API_READ_TOKEN]
    export.py --from-ndjson raw.ndjson <out.json> --label production

Field keys are `<docId>|<path>`, where path addresses arrays by `_key` when the
items carry one, and by index otherwise:

    homeownerDashboard|.faq[_key=de0d4c00bb5b].answer[_key=8dad82d088a9].children[_key=2f1b476dee28].text

`_key` addressing rather than index is essential. Editing one portable-text block
in the Studio can reorder or re-key its siblings; index addressing then reports a
whole array as changed and a patch written against an index can hit the wrong item.

Excluded from comparison: `_rev`, `_updatedAt`, `_createdAt` and the whole
`_system` subtree. `_system.base.rev` changes on every edit, so leaving it in makes
already-synced documents look drifted.

Only content documents are compared. Assets (`sanity.imageAsset`, `sanity.fileAsset`),
drafts and `sanity.*` system documents are captured as metadata for asset resolution
but never diffed as content.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SKIP_TOP = {'_rev', '_updatedAt', '_createdAt', '_system', '_type', '_id'}


def kind(doc_id, doc):
    t = doc.get('_type', '')
    if t in ('sanity.imageAsset', 'sanity.fileAsset'):
        return 'asset'
    if doc_id.startswith('drafts.'):
        return 'draft'
    if doc_id.startswith('system.') or t.startswith('sanity.'):
        return 'system'
    return 'content'


def leaves(node, path, out):
    """Emit (path, scalar) for every leaf, addressing arrays by _key when present."""
    if isinstance(node, dict):
        for k in sorted(node):
            if k in ('_key',):
                continue
            leaves(node[k], f"{path}.{k}", out)
    elif isinstance(node, list):
        keyed = node and all(isinstance(x, dict) and '_key' in x for x in node)
        for i, item in enumerate(node):
            seg = f"[_key={item['_key']}]" if keyed else f"[{i}]"
            leaves(item, f"{path}{seg}", out)
        if not keyed:
            out[f"{path}[len]"] = len(node)
    else:
        out[path] = node


def fetch(project, dataset, token):
    url = f"https://{project}.api.sanity.io/v2021-06-07/data/export/{dataset}"
    r = subprocess.run(['curl', '-sS', '-H', f'Authorization: Bearer {token}', url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"export failed: {r.stderr.strip()}")
    return r.stdout


def build(ndjson, label):
    docs = {}
    for line in ndjson.splitlines():
        line = line.strip()
        if line:
            d = json.loads(line)
            docs[d['_id']] = d

    fields, display, assets, bodies = {}, {}, {}, {}
    counts = {'content': 0, 'asset': 0, 'draft': 0, 'system': 0}
    for doc_id, doc in docs.items():
        k = kind(doc_id, doc)
        counts[k] += 1
        if k == 'asset':
            dim = doc.get('metadata', {}).get('dimensions', {})
            assets[doc_id] = {
                'filename': doc.get('originalFilename'), 'size': doc.get('size'),
                'sha1': doc.get('sha1hash'), 'mime': doc.get('mimeType'),
                'url': doc.get('url'),
                'width': dim.get('width'), 'height': dim.get('height'),
            }
            continue
        if k != 'content':
            continue
        body = {kk: vv for kk, vv in doc.items() if kk not in SKIP_TOP}
        bodies[doc_id] = body
        out = {}
        leaves(body, '', out)
        for path, value in out.items():
            fields[f"{doc_id}|{path}"] = value
        display[doc_id] = {'type': doc.get('_type'), 'updatedAt': doc.get('_updatedAt'),
                           'rev': doc.get('_rev'),
                           'path': doc.get('path'), 'title': doc.get('title')}

    return {
        'meta': {'label': label,
                 'captured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'counts': counts, 'documents': len(docs)},
        'fields': fields, 'display': display, 'assets': assets, 'bodies': bodies,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dataset', nargs='?')
    ap.add_argument('out')
    ap.add_argument('--project', default='5z88vhq0')
    ap.add_argument('--token-env', default='SANITY_API_READ_TOKEN')
    ap.add_argument('--from-ndjson')
    ap.add_argument('--label')
    ap.add_argument('--keep-raw')
    a = ap.parse_args()

    if a.from_ndjson:
        raw = open(a.from_ndjson).read()
        label = a.label or a.from_ndjson
    else:
        token = os.environ.get(a.token_env)
        if not token:
            for f in ('.env.local', '.env'):
                if os.path.exists(f):
                    for line in open(f):
                        if line.startswith(a.token_env + '='):
                            token = line.split('=', 1)[1].strip().strip('"\'')
                            break
                if token:
                    break
        if not token:
            sys.exit(f"no {a.token_env} in environment, .env.local or .env")
        raw = fetch(a.project, a.dataset, token)
        label = a.label or a.dataset
        if a.keep_raw:
            open(a.keep_raw, 'w').write(raw)

    snap = build(raw, label)
    with open(a.out, 'w') as fh:
        json.dump(snap, fh, indent=1, default=str)
    c = snap['meta']['counts']
    print(f"{label}: {snap['meta']['documents']} documents "
          f"({c['content']} content, {c['asset']} assets, {c['draft']} drafts, {c['system']} system) "
          f"-> {len(snap['fields'])} comparable fields")


if __name__ == '__main__':
    main()
