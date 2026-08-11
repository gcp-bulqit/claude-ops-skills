#!/usr/bin/env python3
"""Capture the notification content of one Neon branch as a comparable snapshot.

Usage:
    snapshot.py <postgres-uri> <out.json> [--label NAME]

The snapshot is a flat map of field keys to values, plus display metadata. Field
keys are stable across branches so the same key can be compared three ways:

    template:<code>:<column>       notification_templates own content columns
    link:<code>:<column>           email_id / dashboard_alert_id / inbox_message_id
    email:<id>:<column>            notification_template_emails
    alert:<id>:<column>            notification_template_dashboard_alerts
    inbox:<id>:<column>            notification_template_inbox_messages
    cancellation:<a>-><b>          notification_template_cancellations

Content rows are keyed by id, not by template code, because email_id is NOT
unique: one email row can back two templates, and two templates reconciled in
opposite directions would overwrite each other. Keying by id also covers rows no
template references at all.

Bookkeeping columns (created_at, updated_at, *_admin_id) are captured for display
only. They are never compared: a sync applied as direct SQL bumps neither them nor
notification_template_history, so an untouched timestamp is not evidence a row is
unchanged.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

TEMPLATE_CONTENT = [
    'label', 'description', 'target', 'status', 'sms', 'event_name',
    'subject_type', 'conditions', 'recipient_strategy', 'recipient_params',
    'category', 'scheduling_type', 'scheduling_delay_seconds', 'repeat_policy',
]
TEMPLATE_LINK = ['email_id', 'dashboard_alert_id', 'inbox_message_id']
TEMPLATE_BOOK = ['created_at', 'updated_at', 'created_by_admin_id', 'updated_by_admin_id']

EMAIL_COLS = ['subject', 'content', 'from_email']
ALERT_COLS = ['title', 'message', 'action_label', 'action_url', 'icon', 'severity_for_admin']
INBOX_COLS = ['body', 'conversation_strategy']

QUERIES = {
    'templates': "select row_to_json(t) from notification_templates t order by code",
    'emails': "select row_to_json(e) from notification_template_emails e order by id",
    'alerts': "select row_to_json(a) from notification_template_dashboard_alerts a order by id",
    'inbox': "select row_to_json(i) from notification_template_inbox_messages i order by id",
    'cancellations': ("select row_to_json(c) from notification_template_cancellations c "
                      "order by cancelling_template_id, cancelled_template_id"),
    'history': ("select json_build_object('template_id', h.notification_template_id, "
                "'admin_id', h.admin_id, 'created_at', h.created_at, 'description', h.description) "
                "from notification_template_history h order by h.created_at"),
}


def psql(uri, sql):
    """Run a query and return parsed JSON rows.

    PGHOST/PGPORT/PGDATABASE/PGUSER are stripped: a devenv shell exports them and
    they silently override the URI, sending the query to the local database.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER')}
    out = subprocess.run(['psql', uri, '-Atq', '-c', sql],
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        sys.exit(f"psql failed:\n{out.stderr.strip()}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def build(uri, label):
    rows = {name: psql(uri, sql) for name, sql in QUERIES.items()}

    fields, display = {}, {}
    by_code, id_to_codes = {}, {}

    for t in rows['templates']:
        code = t['code']
        by_code[code] = t
        for c in TEMPLATE_CONTENT:
            fields[f"template:{code}:{c}"] = t.get(c)
        for c in TEMPLATE_LINK:
            fields[f"link:{code}:{c}"] = t.get(c)
        display[code] = {
            'template_id': t['id'], 'label': t.get('label'),
            **{c: t.get(c) for c in TEMPLATE_BOOK},
            **{c: t.get(c) for c in TEMPLATE_LINK},
        }
        for c, kind in (('email_id', 'email'), ('dashboard_alert_id', 'alert'),
                        ('inbox_message_id', 'inbox')):
            if t.get(c) is not None:
                id_to_codes.setdefault(f"{kind}:{t[c]}", []).append(code)

    for r in rows['emails']:
        for c in EMAIL_COLS:
            fields[f"email:{r['id']}:{c}"] = r.get(c)
    for r in rows['alerts']:
        for c in ALERT_COLS:
            fields[f"alert:{r['id']}:{c}"] = r.get(c)
    for r in rows['inbox']:
        for c in INBOX_COLS:
            fields[f"inbox:{r['id']}:{c}"] = r.get(c)
    for r in rows['cancellations']:
        key = f"cancellation:{r['cancelling_template_id']}->{r['cancelled_template_id']}"
        fields[key] = True

    latest_history = {}
    for h in rows['history']:
        tid = h['template_id']
        prev = latest_history.get(tid)
        if prev is None or h['created_at'] > prev['created_at']:
            latest_history[tid] = h

    return {
        'meta': {
            'label': label,
            'captured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'counts': {k: len(v) for k, v in rows.items()},
        },
        'fields': fields,
        'display': display,
        'owners': id_to_codes,
        'latest_history': {str(k): v for k, v in latest_history.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('uri')
    ap.add_argument('out')
    ap.add_argument('--label', default='')
    a = ap.parse_args()
    snap = build(a.uri, a.label or a.out)
    with open(a.out, 'w') as fh:
        json.dump(snap, fh, indent=1, default=str)
    c = snap['meta']['counts']
    print(f"{a.label or a.out}: {c['templates']} templates, {c['emails']} emails, "
          f"{c['alerts']} alerts, {c['inbox']} inbox, {c['cancellations']} cancellations, "
          f"{c['history']} history rows -> {len(snap['fields'])} comparable fields")


if __name__ == '__main__':
    main()
