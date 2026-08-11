#!/usr/bin/env python3
"""Turn a content-rewriting migration into read-only match counts.

A content migration's guards tell you what it intends. Only the target database
tells you what it will do, because template copy drifts per environment: a
statement whose pattern no longer matches is a silent no-op, and a statement with
no code filter reaches rows nobody listed.

Rewrites each `UPDATE <table> ... WHERE <cond>` as `SELECT count(*) FROM <table>
WHERE <cond>`, preserving the guard verbatim. Pure SELECT, safe against
production. Run the emitted file with psql; do not run the migration itself
against production to find this out.

  python3 dryrun_sql.py --ref development --tag 0543_n057_next_visit_note \
    > dryrun.sql
  psql "$PROD_URI" -f dryrun.sql
"""
import argparse
import re
import subprocess
import sys

MIG_DIR = 'packages/database/migrations'
UPDATE_RE = re.compile(r'UPDATE\s+"?(?P<table>[\w]+)"?', re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='development')
    ap.add_argument('--tag', required=True, help='migration tag, e.g. 0543_n057_next_visit_note')
    a = ap.parse_args()

    out = subprocess.run(f'git show origin/{a.ref}:{MIG_DIR}/{a.tag}.sql',
                         shell=True, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip())

    selects, n = [], 0
    for stmt in out.stdout.split('--> statement-breakpoint'):
        m = UPDATE_RE.search(stmt)
        if not m:
            continue
        n += 1
        if not re.search(r'\bWHERE\b', stmt, re.I):
            selects.append(f"SELECT {n} AS stmt, '{m.group('table')}' AS tbl, "
                           f"count(*) AS rows_matched, 'NO WHERE CLAUSE' AS scope "
                           f"FROM {m.group('table')}")
            continue
        where = stmt[re.search(r'\bWHERE\b', stmt, re.I).start():].strip().rstrip(';').rstrip()
        # `code IN (...)` scopes just as well as `code = '...'`. Treating only the
        # equality form as scoped cries wolf on statements that name their templates.
        scoped = re.search(r'\bcode\s*(=|IN\s*\()', where, re.I)
        scope = 'code-scoped' if scoped else 'UNSCOPED: whole table'
        selects.append(f"SELECT {n} AS stmt, '{m.group('table')}' AS tbl, "
                       f"count(*) AS rows_matched, '{scope}' AS scope "
                       f"FROM {m.group('table')} {where}")

    if not selects:
        sys.exit(f'{a.tag} contains no UPDATE statements; nothing to dry-run.')

    print(f"-- Read-only dry run of {a.tag} ({n} UPDATE statements).")
    print("-- rows_matched is what the migration will actually change on this database.")
    print("-- 0 means production copy has drifted from the guard and that statement is a no-op.")
    print("-- An UNSCOPED row rewrites every matching row in the table, not just the")
    print("-- templates the migration names. Identify those rows before merging.")
    print("\\pset pager off")
    print('\nUNION ALL\n'.join(selects) + '\nORDER BY 1;')


if __name__ == '__main__':
    main()
