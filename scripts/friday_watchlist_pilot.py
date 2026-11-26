"""Record manually confirmed watchlist sightings in a standalone pilot database."""
import argparse
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def parse_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Include the UTC offset')
    return stamp.astimezone(timezone.utc)


def record(path, event, symbol, allowed, first_seen, published=None, clock=None):
    if not re.fullmatch(r'[A-Z]{1,5}(?:[.-][A-Z])?', symbol) or symbol not in allowed:
        raise ValueError('Verify the exact symbol against the approved stock list')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', event):
        raise ValueError('Use a short stable event identifier')
    now = clock or datetime.now(timezone.utc)
    seen = parse_time(first_seen)
    source = parse_time(published) if published else None
    if seen > now or (source and source > seen):
        raise ValueError('Verify the capture and source clocks')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS sightings (
            event TEXT PRIMARY KEY, symbol TEXT NOT NULL, seen TEXT NOT NULL,
            recorded TEXT NOT NULL, published TEXT)""")
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO sightings VALUES(?,?,?,?,?)',
                   (event,symbol,seen.isoformat(),now.isoformat(),None))
        saved_symbol, saved_seen, truth = db.execute(
            'SELECT symbol,seen,published FROM sightings WHERE event=?', (event,)).fetchone()
        if saved_symbol != symbol or (source and truth and source != parse_time(truth)):
            raise ValueError('Conflicting evidence for existing event')
        earliest = min(seen, parse_time(saved_seen))
        verified = source or (parse_time(truth) if truth else None)
        if verified and verified > earliest:
            raise ValueError('Source timestamp conflicts with first sighting')
        db.execute('UPDATE sightings SET seen=?, published=? WHERE event=?',
                   (earliest.isoformat(), verified.isoformat() if verified else None,event))
        return {'event':event,'symbol':symbol,'first_seen':earliest.isoformat(),
                'delay_seconds':(earliest-verified).total_seconds() if verified else None}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('event','symbol','first-seen','allowlist','pilot-db'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--published')
    args = parser.parse_args()
    target = Path(args.pilot_db)
    if target.name != 'watchlist-pilot.sqlite3':
        parser.error('Use a separate watchlist-pilot.sqlite3 file')
    allowed = set(Path(args.allowlist).read_text().splitlines())
    print(json.dumps(record(target,args.event,args.symbol,allowed,args.first_seen,args.published)))
