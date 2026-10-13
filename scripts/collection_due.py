#!/usr/bin/env python3
"""Detect missed daily collection independently of the review queue.

All callers must hold the innovation-news-collection concurrency lock and
checkout main immediately before evaluating this gate.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
ATTEMPT = ROOT / 'data/collection_attempt.json'


def timestamp(value):
    value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timestamp must have a timezone')
    return value


def evaluate(run_log, source_status, attempt, now, manual_retry=False):
    local = now.astimezone(JST)
    due_at = local.replace(hour=3, minute=17, second=0, microsecond=0)
    if local < due_at:
        return False, 'Before daily collection time (03:17 JST)'
    # Reviews and weekly/targeted collections cannot stand in for daily collection.
    daily = [timestamp(r['run_at']) for r in run_log['runs']
             if r.get('note') == 'Daily update' and not r.get('targeted_sources')]
    source_at = timestamp(source_status['updated_at'])
    if source_at > now + timedelta(minutes=5) or any(t > now + timedelta(minutes=5) for t in daily):
        raise ValueError('Future collection timestamp')
    if daily and max(daily) >= due_at and source_at >= due_at:
        return False, 'Daily collection already persisted for this JST date'
    if not manual_retry and attempt.get('started_at'):
        started = timestamp(attempt['started_at'])
        if started > now + timedelta(minutes=5):
            raise ValueError('Future attempt timestamp')
        if now < started + timedelta(hours=2):
            return False, 'Previous collection attempt is within the two-hour retry interval'
    return True, 'Daily collection missing; recover it before reviewing candidates'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mark-attempt', action='store_true')
    parser.add_argument('--manual-retry', action='store_true', help='User-requested retry; bypass only local attempt interval, never API guards or date deduplication')
    parser.add_argument('--force', action='store_true', help='Explicit historical backfill only')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.mark_attempt:
        ATTEMPT.write_text(json.dumps({'started_at': now.isoformat()}, indent=2) + '\n')
        return
    attempt = read(ATTEMPT) if ATTEMPT.exists() else {}
    due, reason = evaluate(read(ROOT / 'data/run_log.json'),
                           read(ROOT / 'data/source_status.json'), attempt, now, manual_retry=args.manual_retry)
    if args.force:
        due, reason = True, 'Explicit historical backfill requested'
    print(f'Collection due={due}: {reason}')
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f'should_run={str(due).lower()}\n')


if __name__ == '__main__':
    main()
