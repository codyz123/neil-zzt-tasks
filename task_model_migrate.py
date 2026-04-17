#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import requests

SUPABASE_URL = 'https://qqlqqknlqqdyukxoyyty.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxbHFxa25scXFkeXVreG95eXR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI1NTYzOTQsImV4cCI6MjA4ODEzMjM5NH0.jZBd3dM1aNjRqDXxL0a3p0V8BPtfdZ8I4LaAKN4_1mk'
MODEL_KIND_PREFIX = 'kind:'
ITEM_KIND = {'project', 'task', 'reminder', 'project_task'}
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=representation',
}


def low_tags(task):
    return [str(tag).lower() for tag in (task.get('tags') or [])]


def explicit_kind(task):
    for tag in low_tags(task):
        if tag.startswith(MODEL_KIND_PREFIX):
            kind = tag[len(MODEL_KIND_PREFIX):]
            if kind in ITEM_KIND:
                return kind
    return None


def project_id(task):
    if task.get('parent_id'):
        return task['parent_id']
    depends_on = task.get('depends_on')
    if isinstance(depends_on, list):
        return depends_on[0] if depends_on else None
    return depends_on or None


def infer_kind(task, known_ids=None):
    kind = explicit_kind(task)
    if kind:
        return kind
    pid = project_id(task)
    if pid and (known_ids is None or pid in known_ids):
        return 'project_task'
    tags = low_tags(task)
    if 'project' in tags:
        return 'project'
    if 'reminder' in tags:
        return 'reminder'
    return 'task'


def canonical_tags(task, kind):
    keep = []
    for tag in (task.get('tags') or []):
        lower = str(tag).lower()
        if lower.startswith(MODEL_KIND_PREFIX):
            continue
        if lower in {'project', 'reminder'}:
            continue
        keep.append(tag)
    if kind == 'project':
        keep.append('project')
    if kind == 'reminder':
        keep.append('reminder')
    keep.append(f'{MODEL_KIND_PREFIX}{kind}')
    return keep


def in_scope(task):
    values = [task.get('created_at'), task.get('due_date'), task.get('scheduled_date')]
    tags = low_tags(task)
    if any(str(value or '').startswith('2026-05') for value in values):
        return True
    if '2026-05' in tags:
        return True
    if task.get('parent_id') or task.get('depends_on'):
        return True
    if 'project' in tags or 'reminder' in tags:
        return True
    return False


def build_patch(task, known_ids=None):
    kind = infer_kind(task, known_ids)
    pid = project_id(task) if kind == 'project_task' and project_id(task) in (known_ids or {project_id(task)}) else None
    patch = {
        'tags': canonical_tags(task, kind),
        'parent_id': pid,
        'depends_on': [pid] if pid else None,
    }
    return patch


def changed(task, patch):
    current = {
        'tags': task.get('tags') or None,
        'parent_id': task.get('parent_id'),
        'depends_on': task.get('depends_on'),
    }
    candidate = deepcopy(patch)
    if not current['tags']:
        current['tags'] = None
    if not candidate['tags']:
        candidate['tags'] = None
    return current != candidate


def fetch_tasks():
    res = requests.get(f'{SUPABASE_URL}/rest/v1/tasks?select=*&order=created_at.asc&limit=1000', headers=HEADERS, timeout=30)
    res.raise_for_status()
    return res.json()


def patch_task(task_id, patch):
    res = requests.patch(f'{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}', headers=HEADERS, data=json.dumps(patch), timeout=30)
    res.raise_for_status()
    return res.json()


def main():
    parser = argparse.ArgumentParser(description='Normalize task item model.')
    parser.add_argument('--apply', action='store_true', help='Write changes to Supabase')
    args = parser.parse_args()

    tasks = fetch_tasks()
    known_ids = {task['id'] for task in tasks}
    backup_dir = Path(__file__).resolve().parent / 'backups'
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"tasks-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    backup_path.write_text(json.dumps(tasks, indent=2))

    scoped = [task for task in tasks if in_scope(task)]
    updates = []
    by_kind = Counter()
    for task in scoped:
        kind = infer_kind(task, known_ids)
        by_kind[kind] += 1
        patch = build_patch(task, known_ids)
        if changed(task, patch):
            updates.append((task, patch))

    print(f'Backed up {len(tasks)} tasks to {backup_path}')
    print(f'Scoped records: {len(scoped)}')
    print('Kind counts:', dict(by_kind))
    print(f'Pending updates: {len(updates)}')
    for task, patch in updates:
        print(f"- {task['title']} :: {task['id']}")
        print(json.dumps(patch, ensure_ascii=False))

    if not args.apply:
        print('\nDry run only. Re-run with --apply to write changes.')
        return

    for task, patch in updates:
        patch_task(task['id'], patch)
    print(f'Applied {len(updates)} updates.')


if __name__ == '__main__':
    main()
