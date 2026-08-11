#!/usr/bin/env python3
"""Classify the migrations a promotion carries and flag the hazards that have bitten.

Reads migrations straight out of the git trees, so it never needs a checkout.
Emits JSON. Nothing here writes, and it does not connect to a database; pass
--hwm to compare against a high-water mark you read separately.

The three hazards it knows about are the three that have actually happened:

  backfill_vs_unique_index  A migration fills a column that an existing partial
                            or full unique index covers. Harmless while the
                            column is all NULLs (NULLs are distinct); the
                            backfill is what can violate it. This is 0542
                            against 0539's service_jobs_one_time_live_uq.

  content_rewrite           A migration rewrites notification template copy,
                            introducing tokens whose loaders may not exist on
                            either slot yet. Renders as an empty string, so it
                            never errors and never alerts. This is 0540/N063
                            and 0543/next_visit_note.

  enum_add_and_use          ALTER TYPE ADD VALUE and a use of that value in one
                            migration. Fails 55P04 because both land in one
                            transaction.
"""
import argparse
import json
import re
import subprocess
import sys

SLOTS = ('blue', 'green')
MIG_DIR = 'packages/database/migrations'
UNIQUE_IDX_RE = re.compile(
    r'CREATE\s+UNIQUE\s+INDEX\s+"?(?P<name>[\w]+)"?\s+ON\s+"?(?P<table>[\w]+)"?'
    r'[^(]*\((?P<cols>[^)]*)\)(?P<pred>[^;]*)', re.I)
UPDATE_RE = re.compile(r'UPDATE\s+"?(?P<table>[\w]+)"?', re.I)
SET_COL_RE = re.compile(r'SET\s+"?(?P<col>[\w]+)"?\s*=', re.I)
TOKEN_RE = re.compile(r'\{\{(\w+)\}\}')


def run(cmd, check=True):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and out.returncode != 0:
        sys.exit(f"failed: {cmd}\n{out.stderr.strip()}")
    return out.stdout


def journal(ref):
    return json.loads(run(f'git show origin/{ref}:{MIG_DIR}/meta/_journal.json'))


def sql(ref, tag):
    return run(f'git show origin/{ref}:{MIG_DIR}/{tag}.sql', check=False)


def all_unique_indexes(ref, entries):
    """Every unique index the migration history creates, with its columns and predicate."""
    found = {}
    for e in entries:
        body = sql(ref, e['tag'])
        for m in UNIQUE_IDX_RE.finditer(body):
            cols = [c.strip().strip('"').split()[0] for c in m.group('cols').split(',') if c.strip()]
            pred = ' '.join(m.group('pred').split())
            found[m.group('name')] = {
                'table': m.group('table'), 'columns': cols,
                'predicate': pred, 'created_in': e['tag'],
            }
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', required=True, help='slot being promoted into')
    ap.add_argument('--source', default='development')
    ap.add_argument('--hwm', type=int, default=None,
                    help="production MAX(created_at) from drizzle.__drizzle_migrations")
    a = ap.parse_args()

    other = SLOTS[0] if a.target == SLOTS[1] else SLOTS[1]
    src_entries = journal(a.source)['entries']
    tgt_entries = journal(a.target)['entries']
    tgt_tags = {e['tag'] for e in tgt_entries}
    tgt_max_when = max((e['when'] for e in tgt_entries), default=0)

    new = [e for e in src_entries if e['tag'] not in tgt_tags]
    indexes = all_unique_indexes(a.source, src_entries)

    report, hazards = [], []
    prev_when = None
    for e in new:
        body = sql(a.source, e['tag'])
        upper = body.upper()
        kinds = []
        if re.search(r'UPDATE\s+"?notification_template', body, re.I):
            kinds.append('content_rewrite')
        elif UPDATE_RE.search(body):
            kinds.append('backfill')
        if 'ALTER TABLE' in upper or 'CREATE INDEX' in upper or 'CREATE UNIQUE INDEX' in upper:
            kinds.append('ddl')
        if 'ALTER TYPE' in upper and 'ADD VALUE' in upper:
            kinds.append('enum_add')

        entry = {'idx': e['idx'], 'tag': e['tag'], 'when': e['when'], 'kinds': kinds,
                 'runs_on_this_promotion': a.hwm is None or e['when'] > a.hwm}

        if prev_when is not None and e['when'] <= prev_when:
            hazards.append({'kind': 'journal_order', 'tag': e['tag'],
                            'detail': f"when {e['when']} does not exceed the previous entry's "
                                      f"{prev_when}; the runner selects by when > HWM, so this "
                                      f"file is silently skipped."})
        if e['when'] <= tgt_max_when:
            entry['runs_on_this_promotion'] = False
            entry['note'] = (f"when is at or below the target slot's own journal max "
                             f"({tgt_max_when}); already applied.")
        prev_when = e['when']

        # content rewrite: which tokens does it introduce, and do the slots have them?
        if 'content_rewrite' in kinds:
            replacements = re.findall(r"'(\{\{\w+\}\}[^']*)'", body)
            tokens = sorted({t for r in replacements for t in TOKEN_RE.findall(r)})
            entry['tokens_introduced'] = tokens
            entry['codes_targeted'] = sorted(set(re.findall(r"code\s*=\s*'([A-Z0-9]+)'", body)))
            # Number only the UPDATE-bearing segments, so these indexes match the
            # `stmt` column dryrun_sql.py emits. Numbering every segment would put
            # the two tools out of step and send someone to the wrong statement.
            unscoped, n_stmt = [], 0
            # (table, column) pairs this migration writes. The backup MUST be built
            # from this, never from an assumption about which tables hold copy.
            # `notification_templates` itself carries the `sms` column, so a pattern
            # matching only `notification_template_*` silently misses every SMS
            # rewrite and produces a pre-image that cannot restore them.
            targets = {}
            for s in body.split('--> statement-breakpoint'):
                m2 = re.search(r'UPDATE\s+"?(notification_\w+)"?', s, re.I)
                if not m2:
                    continue
                n_stmt += 1
                tbl = m2.group(1)
                for col in SET_COL_RE.findall(s):
                    targets.setdefault(tbl, set()).add(col)
                if not re.search(r'\bcode\s*(=|IN\s*\()', s, re.I):
                    unscoped.append(n_stmt)
            entry['content_targets'] = {t: sorted(c) for t, c in targets.items()}
            entry['update_statements'] = n_stmt
            if unscoped:
                hazards.append({
                    'kind': 'unscoped_content_statement', 'tag': e['tag'],
                    'detail': f"statement(s) {unscoped} rewrite notification content with no "
                              f"template-code filter, so they reach every row in the table that "
                              f"matches. Run dryrun_sql.py to see what they actually hit."})
            for tok in tokens:
                presence = {}
                for slot in SLOTS:
                    hits = run(f"git grep -l '{tok}' origin/{slot} -- packages 2>/dev/null", check=False)
                    presence[slot] = bool(hits.strip())
                # The window is rendered by whichever slot is still serving while the
                # promoted slot builds, which is the other slot. Keying the hazard on
                # "no slot has it" would miss the case where the target has the loader
                # and the serving slot does not, which is exactly the dangerous one.
                if not presence.get(other, True):
                    hazards.append({
                        'kind': 'content_rewrite', 'tag': e['tag'], 'token': tok,
                        'serving_slot_missing_loader': other,
                        'detail': f"{{{{{tok}}}}} has no loader on '{other}', the slot that "
                                  f"keeps serving while '{a.target}' builds. The renderer "
                                  f"degrades an unresolvable token to an empty string, so the "
                                  f"copy silently loses that clause from the moment this "
                                  f"migration commits until the worker is redeployed from "
                                  f"'{a.target}' after cutover."})
                entry.setdefault('token_presence', {})[tok] = presence

        # backfill vs an existing unique index
        if 'backfill' in kinds:
            tbl = UPDATE_RE.search(body).group('table')
            cols = set(SET_COL_RE.findall(body))
            entry['backfills'] = {'table': tbl, 'columns': sorted(cols)}
            for name, ix in indexes.items():
                if ix['table'] == tbl and cols & set(ix['columns']):
                    hazards.append({
                        'kind': 'backfill_vs_unique_index', 'tag': e['tag'], 'index': name,
                        'created_in': ix['created_in'], 'table': tbl,
                        'columns': sorted(cols & set(ix['columns'])),
                        'predicate': ix['predicate'],
                        'detail': f"fills {tbl}.{sorted(cols & set(ix['columns']))} which "
                                  f"{name} covers. While the column was all NULL the index "
                                  f"could not conflict; this backfill is what can violate it. "
                                  f"Write a pre-flight that applies this migration's own "
                                  f"attribution rule to the rows the index predicate covers "
                                  f"and expects no group above one."})

        if 'enum_add' in kinds and re.search(r"INSERT|UPDATE", body, re.I):
            hazards.append({'kind': 'enum_add_and_use', 'tag': e['tag'],
                            'detail': "adds an enum value and uses it in the same migration, "
                                      "so both land in one transaction and it fails 55P04. "
                                      "Split into two migrations."})
        report.append(entry)

    print(json.dumps({
        'target': a.target, 'other_slot': other,
        'journal_tips': {b: journal(b)['entries'][-1]['tag'] for b in (a.source,) + SLOTS},
        'production_hwm': a.hwm,
        'target_journal_max_when': tgt_max_when,
        'new_migrations': report,
        'runs_count': sum(1 for r in report if r['runs_on_this_promotion']),
        'hazards': hazards,
    }, indent=2))


if __name__ == '__main__':
    main()
