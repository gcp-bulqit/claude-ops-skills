#!/usr/bin/env python3
"""Turn an approved plan into Sanity patch mutations.

Usage:
    build_mutations.py --plan plan.json --dev dev.json --prod prod.json \
                       --out mutations.json [--allow-upload]

Only keys in plan['push'] are written. Path B means editing that list first.

How a patch path is chosen
--------------------------
A changed leaf is patched at its own path when that path resolves on BOTH sides.
When it does not -- an array item present on one side only -- the patch escalates
to the nearest ancestor that resolves on both, which in practice means writing a
whole array.

A field production holds and development does not is a deletion, which `set`
cannot express. Those become `unset` on the same document patch. An unset nested
inside a subtree being written whole is dropped, because the set already carries
development's version of it.

Escalation is where content gets destroyed, so every escalated root is checked:
if writing it would also overwrite a field that is NOT approved (a PRESERVE, a
CONFLICT you did not resolve development's way, or a SETTLED divergence), the
script refuses and names the field. That check is what stops a whole-array copy
from silently deleting a production-only entry.

Other guarantees
----------------
- One patch per document, so two patches cannot collide on ifRevisionID inside a
  single transaction.
- Every patch carries ifRevisionID, so a concurrent Studio edit fails the whole
  transaction rather than being overwritten.
- Asset references classified CHURN keep production's id: development's is never
  written, because it would not resolve.
- Asset references classified UPLOAD are refused unless --allow-upload, and even
  then the upload itself is done by upload_assets.py first, which rewrites the
  plan with the new production ids.
"""
import argparse
import json
import re
import sys

SEG = re.compile(r'\[_key=([^\]]+)\]|\[(\d+)\]|\.([^.\[]+)')


def parse(path):
    out = []
    for m in SEG.finditer(path):
        if m.group(1) is not None:
            out.append(('key', m.group(1)))
        elif m.group(2) is not None:
            out.append(('idx', int(m.group(2))))
        else:
            out.append(('field', m.group(3)))
    return out


def resolve(body, segs):
    """Return (found, value) for a parsed path against a document body."""
    node = body
    for kind, val in segs:
        if kind == 'field':
            if not isinstance(node, dict) or val not in node:
                return False, None
            node = node[val]
        elif kind == 'idx':
            if not isinstance(node, list) or val >= len(node):
                return False, None
            node = node[val]
        else:
            if not isinstance(node, list):
                return False, None
            hit = next((x for x in node
                        if isinstance(x, dict) and x.get('_key') == val), None)
            if hit is None:
                return False, None
            node = hit
    return True, node


def refs_in(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if k == '_ref' and isinstance(v, str):
                out.append(v)
            else:
                refs_in(v, out)
    elif isinstance(node, list):
        for x in node:
            refs_in(x, out)
    return out


def remap_refs(node, remap):
    if not remap:
        return node
    if isinstance(node, dict):
        return {k: (remap.get(v, v) if k == '_ref' and isinstance(v, str)
                    else remap_refs(v, remap)) for k, v in node.items()}
    if isinstance(node, list):
        return [remap_refs(x, remap) for x in node]
    return node


def to_sanity(segs):
    out = ''
    for kind, val in segs:
        if kind == 'field':
            out += ('.' if out else '') + val
        elif kind == 'idx':
            out += f"[{val}]"
        else:
            out += f'[_key=="{val}"]'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', required=True)
    ap.add_argument('--dev', required=True)
    ap.add_argument('--prod', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--allow-upload', action='store_true')
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    dev, prod = json.load(open(a.dev)), json.load(open(a.prod))
    push = plan['push']
    if not push:
        sys.exit("plan['push'] is empty; nothing to build")

    assets = plan.get('assets', {})
    pending = [k for k in push if assets.get(k, {}).get('verdict') == 'UPLOAD']
    if pending and not a.allow_upload:
        sys.exit("these keys reference assets that do not exist in production:\n  "
                 + "\n  ".join(pending)
                 + "\nRun upload_assets.py first, or drop them from plan['push'].")
    churn = {k for k, v in assets.items() if v.get('verdict') == 'CHURN'}
    skipped_churn = [k for k in push if k in churn]
    push = [k for k in push if k not in churn]

    approved = set(push)
    not_approved = set(plan.get('preserve', [])) | set(plan.get('conflict', [])) \
        | set(plan.get('unclassified', [])) | set(plan.get('settled', [])) | churn

    # Any value written may contain asset references, including inside an escalated
    # subtree. Development ids must be rewritten to production's before writing:
    # CHURN -> production's existing id for the same artwork; UPLOAD -> the id
    # upload_assets.py recorded after uploading.
    remap = dict(plan.get('asset_remap', {}))
    for k, v in assets.items():
        if v.get('verdict') == 'CHURN' and v.get('prod_equivalent'):
            dev_ref = dev['fields'].get(k)
            if isinstance(dev_ref, str):
                remap[dev_ref] = v['prod_equivalent']

    by_doc = {}
    for k in push:
        doc, _, path = k.partition('|')
        by_doc.setdefault(doc, []).append(path)

    mutations, refusals, summary = [], [], []
    for doc, paths in sorted(by_doc.items()):
        db, pb = dev['bodies'].get(doc), prod['bodies'].get(doc)
        if db is None or pb is None:
            refusals.append(f"{doc}: document missing on one side; create it by hand")
            continue
        roots, drops = {}, {}
        for path in paths:
            full = parse(path)
            segs = list(full)
            while segs:
                in_dev, _ = resolve(db, segs)
                in_prod, _ = resolve(pb, segs)
                if in_dev and in_prod:
                    break
                segs = segs[:-1]
            # segs is the longest prefix resolving on BOTH sides; [] means the
            # document root, which is always a common object.
            if len(segs) == len(full):
                roots[to_sanity(segs)] = segs
                continue

            # The paths diverge at this segment. A field production has and
            # development does not is a deletion, and `set` cannot say that: the
            # old code shrank the prefix to nothing and refused with a misleading
            # "no common ancestor", so every removed field had to be patched by
            # hand. Emit `unset` instead.
            nxt = full[:len(segs) + 1]
            kind = nxt[-1][0]
            in_dev, _ = resolve(db, nxt)
            in_prod, _ = resolve(pb, nxt)
            if kind == 'field' and in_prod and not in_dev:
                drops[to_sanity(nxt)] = nxt
            elif segs:
                # Array item present on one side only, or a container whose type
                # differs between sides. Escalate to the common container and write
                # it whole, which is the pre-existing behaviour.
                roots[to_sanity(segs)] = segs
            elif kind == 'field' and in_dev:
                # New top-level field. The document root is an object on both
                # sides, so setting the field directly is safe.
                roots[to_sanity(nxt)] = nxt
            else:
                refusals.append(f"{doc}|{path}: no common ancestor; patch the document by hand")

        # drop roots nested inside another root
        keep = [r for r in roots if not any(r != o and r.startswith(o + '.')
                                            or r != o and r.startswith(o + '[')
                                            for o in roots)]
        sets = {}
        for r in sorted(keep):
            segs = roots[r]
            prefix = f"{doc}|{''.join(('.' + v) if k == 'field' else (f'[_key={v}]' if k == 'key' else f'[{v}]') for k, v in segs)}"
            covered = [k for k in dev['fields'] if k == prefix or k.startswith(prefix + '.')
                       or k.startswith(prefix + '[')]
            covered += [k for k in prod['fields']
                        if (k == prefix or k.startswith(prefix + '.') or k.startswith(prefix + '['))
                        and k not in covered]
            clobbered = [k for k in covered if k in not_approved]
            if clobbered:
                refusals.append(
                    f"{doc}: writing `{r}` would also overwrite {len(clobbered)} "
                    f"un-approved field(s), e.g. {clobbered[:3]}")
                continue
            found, value = resolve(db, segs)
            value = remap_refs(value, remap)
            missing = [x for x in refs_in(value) if x not in prod['assets']]
            if missing:
                refusals.append(
                    f"{doc}: `{r}` carries {len(missing)} asset reference(s) that do not "
                    f"exist in production, e.g. {missing[:2]}. Writing it would render "
                    f"broken images. Upload them first or leave this field out.")
                continue
            sets[r] = value
            summary.append(f"{doc}  set {r}")

        # An unset nested inside a root being written whole is redundant, and
        # ordering it against that set is undefined. The set already carries
        # development's version of the subtree.
        unsets = sorted(d for d in drops
                        if not any(d.startswith(r + '.') or d.startswith(r + '[')
                                   for r in sets))
        summary.extend(f"{doc}  unset {d}" for d in unsets)

        if sets or unsets:
            patch = {'id': doc, 'ifRevisionID': prod['display'][doc]['rev']}
            if sets:
                patch['set'] = sets
            if unsets:
                patch['unset'] = unsets
            mutations.append({'patch': patch})

    if refusals:
        print("REFUSED:")
        for r in refusals:
            print("  " + r)
        print()

    if skipped_churn:
        print(f"{len(skipped_churn)} asset reference(s) skipped as churn "
              f"(production keeps its own id, same artwork):")
        for k in skipped_churn:
            print("  " + k)
        print()

    if not mutations:
        sys.exit("no mutations built")

    with open(a.out, 'w') as fh:
        json.dump({'mutations': mutations}, fh, ensure_ascii=False)
    print(f"{len(mutations)} patch(es) across {len(mutations)} document(s) -> {a.out}")
    for line in summary:
        print("  " + line)
    print("\nDry-run before applying:")
    print(f"  curl -sS -X POST \"https://<PROJECT>.api.sanity.io/v2021-06-07/data/mutate/"
          f"production?dryRun=true&returnIds=true\" \\\n"
          f"    -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' \\\n"
          f"    --data-binary @{a.out}")
    return 1 if refusals else 0


if __name__ == '__main__':
    sys.exit(main())
