"""Google Sheets sync worker.

Run from a Toolforge job every 5 minutes (see setup_jobs.sh):

    python3.11 ~/www/python/src/sync_worker.py

The worker never runs in a web request. It:
- reclaims stale 'sending' rows (lease: 30 minutes),
- backfills submissions that have no queue row for an active sync
  (the remainder of a bounded web-request backfill),
- claims pending rows atomically, oldest first, grouped by form,
- appends rows in batches (max 100 rows per batch / per run per form
  in this implementation),
- retries transient/quota failures with exponential backoff
  (1, 5, 15, 60 minutes; at most 20 attempts over 7 days),
- pauses a sync on auth / not-found / forbidden failures and keeps
  its queue for 7 days, so no response is lost from DTOC,
- logs only counts, form IDs and error kinds — never row contents,
  tokens, or Google error bodies.

The worker is safe to run concurrently: claiming is a single atomic
UPDATE, and re-queueing updates an existing SyncQueue row instead of
inserting a duplicate (unique constraint on submission_id).
"""

import os
import sys
from datetime import datetime, timedelta

# Allow running as a script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from google_auth import _service_for, sheet_row_for_submission
from google_sheets import (KIND_AUTH, KIND_FORBIDDEN, KIND_NOT_FOUND,
                           KIND_QUOTA, KIND_TRANSIENT, SHEET_TITLE,
                           SheetsError, append_rows, get_sheet_titles)
from models import (AuditLog, Form, SheetSync, Submission, SyncQueue, db)

MAX_ROWS_PER_RUN = 100
BATCH_SIZE = 100
LEASE_MINUTES = 30
RETRY_DELAYS_MINUTES = [1, 5, 15, 60]
MAX_ATTEMPTS = 20
QUEUE_TTL_DAYS = 7
BACKFILL_BATCH = 500


def _retry_delay(attempts):
    index = min(max(attempts - 1, 0), len(RETRY_DELAYS_MINUTES) - 1)
    return timedelta(minutes=RETRY_DELAYS_MINUTES[index])


def _queue_expired(row, now):
    return (now - row.created_at).days >= QUEUE_TTL_DAYS


def reclaim_stale_sending(now):
    """Return 'sending' rows whose lease expired to 'pending'."""
    stale_at = now - timedelta(minutes=LEASE_MINUTES)
    stale = SyncQueue.query.filter(
        SyncQueue.status == "sending",
        SyncQueue.updated_at <= stale_at).all()
    for row in stale:
        row.status = "pending"
        row.last_error = "worker lease expired"
    if stale:
        db.session.commit()
    return len(stale)


def backfill_missing(now):
    """Queue submissions with no queue row for active syncs.

    This is the remainder of a bounded web-request backfill: only
    submissions newer than the sync's last synced ID are considered,
    and at most BACKFILL_BATCH rows are created per run.
    """
    created = 0
    for sync in SheetSync.query.filter_by(status="active").all():
        if created >= BACKFILL_BATCH:
            break
        query = Submission.query.filter_by(form_id=sync.form_id).order_by(
            Submission.id)
        if sync.last_synced_submission_id:
            query = query.filter(
                Submission.id > sync.last_synced_submission_id)
        for sub in query.all():
            if created >= BACKFILL_BATCH:
                break
            if SyncQueue.query.filter_by(submission_id=sub.id).first():
                continue
            db.session.add(SyncQueue(
                form_id=sync.form_id, submission_id=sub.id))
            created += 1
    if created:
        db.session.commit()
    return created


def claim_pending(now, limit=MAX_ROWS_PER_RUN):
    """Atomically claim pending rows. Returns the claimed rows."""
    rows = (SyncQueue.query
            .filter(SyncQueue.status == "pending")
            .filter((SyncQueue.next_retry_at.is_(None))
                    | (SyncQueue.next_retry_at <= now))
            .order_by(SyncQueue.created_at)
            .limit(limit).all())
    for row in rows:
        row.status = "sending"
        row.attempts += 1
    if rows:
        db.session.commit()
    return rows


def _pause_sync(sync, kind, message):
    sync.status = "error"
    sync.last_error = f"{kind}: {message}"
    db.session.add(AuditLog(
        action="SHEET_EXPORT_FAILED",
        details=f"Form {sync.form_id} Google Sheets sync paused ({kind})"))
    db.session.commit()


def _fail_rows(rows, kind, message, now):
    """Apply a failure to claimed rows: retry, or expire after the TTL."""
    for row in rows:
        if _queue_expired(row, now) or row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.last_error = f"{kind}: {message}"
        else:
            row.status = "pending"
            row.next_retry_at = now + _retry_delay(row.attempts)
            row.last_error = f"{kind}: {message}"
    db.session.commit()


def _sync_form(form, sync, rows, now):
    """Append one form's claimed rows. Returns (sent, failed)."""
    try:
        service = _service_for(sync.created_by)
    except SheetsError as exc:
        _fail_rows(rows, exc.kind, "Google account unavailable", now)
        if exc.kind == KIND_AUTH:
            _pause_sync(sync, exc.kind, "Google account unavailable")
        return 0, len(rows)
    try:
        titles = get_sheet_titles(service, sync.spreadsheet_id)
    except SheetsError as exc:
        _fail_rows(rows, exc.kind, "Could not read spreadsheet", now)
        if exc.kind in (KIND_AUTH, KIND_NOT_FOUND, KIND_FORBIDDEN):
            _pause_sync(sync, exc.kind, "Could not read spreadsheet")
        return 0, len(rows)
    if SHEET_TITLE not in titles:
        _fail_rows(rows, KIND_NOT_FOUND, "Responses tab not found", now)
        _pause_sync(sync, KIND_NOT_FOUND, "Responses tab not found")
        return 0, len(rows)
    # Build rows in submission-ID order.
    built = []
    for row in sorted(rows, key=lambda r: r.submission_id):
        sub = Submission.query.get(row.submission_id)
        if sub is None:
            row.status = "skipped"
            row.last_error = "submission deleted"
            continue
        built.append((row, sheet_row_for_submission(form, sub)))
    db.session.commit()
    if not built:
        return 0, len(rows)
    try:
        append_rows(service, sync.spreadsheet_id, SHEET_TITLE,
                    [r for _row, r in built])
    except SheetsError as exc:
        _fail_rows([r for r, _ in built], exc.kind,
                   "Google Sheets append failed", now)
        if exc.kind == KIND_AUTH:
            _pause_sync(sync, exc.kind, "Google Sheets append failed")
        elif exc.kind in (KIND_NOT_FOUND, KIND_FORBIDDEN):
            # One sheet deleted / access removed must not mark the
            # whole Google account disconnected (Copilot review).
            _pause_sync(sync, exc.kind, "Google Sheets append failed")
        return 0, len(built)
    sent_ids = []
    for row, _ in built:
        row.status = "sent"
        row.last_error = None
        sent_ids.append(row.submission_id)
    sync.last_synced_at = now
    if sent_ids:
        sync.last_synced_submission_id = max(sent_ids)
    db.session.commit()
    return len(built), 0


def run_sync_worker(app=None):
    if app is None:
        app = create_app()
    with app.app_context():
        from google_auth import google_sync_available
        if not google_sync_available():
            print("Google Sheets sync is not enabled; worker exiting.")
            return {"reclaimed": 0, "backfilled": 0, "sent": 0, "failed": 0}
        now = datetime.utcnow()
        reclaimed = reclaim_stale_sending(now)
        backfilled = backfill_missing(now)
        claimed = claim_pending(now)
        sent = failed = 0
        # Group by form, preserving ID order within each form.
        by_form = {}
        for row in claimed:
            by_form.setdefault(row.form_id, []).append(row)
        for form_id, rows in by_form.items():
            form = Form.query.get(form_id)
            sync = SheetSync.query.filter_by(form_id=form_id).first()
            if form is None or sync is None or sync.status != "active":
                for row in rows:
                    row.status = "pending"
                    row.last_error = "sync not active"
                db.session.commit()
                failed += len(rows)
                continue
            form_sent, form_failed = _sync_form(form, sync, rows, now)
            sent += form_sent
            failed += form_failed
        print(f"Sync worker complete. Reclaimed {reclaimed} stale rows, "
              f"backfilled {backfilled}, sent {sent}, failed {failed}.")
        return {"reclaimed": reclaimed, "backfilled": backfilled,
                "sent": sent, "failed": failed}


if __name__ == "__main__":
    run_sync_worker()
