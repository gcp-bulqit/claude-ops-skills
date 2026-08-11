#!/usr/bin/env python3
"""Upload development assets that have no production equivalent, and record the remap.

Usage:
    upload_assets.py --plan plan.json --dev dev.json --target production \
                     [--project 5z88vhq0] [--apply]

Without --apply this only reports what would be uploaded.

Only keys the comparison classified UPLOAD are considered: artwork with no
production equivalent by filename and dimensions. CHURN references are never
uploaded, because production already holds that artwork under its own id.

The uploaded asset gets a DIFFERENT id from development's. Sanity derives the id
from the file's sha1, and the CDN does not serve the bytes that were originally
uploaded: on 2026-08-07 development's record read 315254 bytes with sha1
c5a255a6..., while every fetch of its CDN url returned 269448 bytes hashing to
0a1c0588..., with or without ?dl= and with or without authentication. The two
datasets therefore diverge permanently on any reference uploaded this way. That
is unavoidable, and it is why CHURN is preferred wherever production already has
the artwork.

The new ids are written back into plan['asset_remap'], which build_mutations.py
applies to every value it writes.
"""
import argparse
import json
import os
import subprocess
import sys


def token(env_name='SANITY_API_READ_TOKEN'):
    t = os.environ.get(env_name)
    if t:
        return t
    for f in ('.env.local', '.env'):
        if os.path.exists(f):
            for line in open(f):
                if line.startswith(env_name + '='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    sys.exit(f"no {env_name} available")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', required=True)
    ap.add_argument('--dev', required=True)
    ap.add_argument('--target', default='production')
    ap.add_argument('--project', default='5z88vhq0')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    dev = json.load(open(a.dev))
    tok = token()

    todo = [k for k in plan['push']
            if plan.get('assets', {}).get(k, {}).get('verdict') == 'UPLOAD']
    if not todo:
        print("No approved field references an asset missing from the target. Nothing to upload.")
        return 0

    print(f"** {len(todo)} NEW FILE(S) WILL BE ADDED TO THE {a.target.upper()} DATASET **\n")
    remap = dict(plan.get('asset_remap', {}))
    for k in todo:
        ref = dev['fields'][k]
        meta = dev['assets'].get(ref, {})
        kind = 'images' if ref.startswith('image-') else 'files'
        print(f"{k}\n  {meta.get('filename')}  {meta.get('width')}x{meta.get('height')}  "
              f"{meta.get('size')} B  {meta.get('mime')}\n  development id: {ref}")
        if not a.apply:
            print("  (dry run, not uploaded)\n")
            continue
        blob = subprocess.run(['curl', '-sS', '-L', meta['url']], capture_output=True)
        if blob.returncode != 0 or not blob.stdout:
            sys.exit(f"  download failed for {meta.get('url')}")
        print(f"  downloaded {len(blob.stdout)} bytes"
              + ("" if len(blob.stdout) == meta.get('size')
                 else f"  (CDN re-encoded: record says {meta.get('size')})"))
        url = (f"https://{a.project}.api.sanity.io/v2021-06-07/assets/{kind}/{a.target}"
               f"?filename={meta.get('filename')}")
        r = subprocess.run(['curl', '-sS', '-X', 'POST', url,
                            '-H', f'Authorization: Bearer {tok}',
                            '-H', f"Content-Type: {meta.get('mime', 'application/octet-stream')}",
                            '--data-binary', '@-'],
                           input=blob.stdout, capture_output=True)
        try:
            new = json.loads(r.stdout.decode())['document']
        except Exception:
            sys.exit(f"  upload failed: {r.stdout[:300]!r}")
        remap[ref] = new['_id']
        print(f"  uploaded -> {new['_id']}"
              + ("  (SAME id)" if new['_id'] == ref else "  (different id, as expected)") + "\n")

    if a.apply:
        plan['asset_remap'] = remap
        with open(a.plan, 'w') as fh:
            json.dump(plan, fh, indent=1)
        print(f"plan updated with {len(remap)} remapping(s). "
              f"build_mutations.py will apply them.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
