from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError
from datetime import datetime

db = SQLAlchemy()

class Form(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    schema = db.Column(db.Text, nullable=False) # JSON string of Form.io schema
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(255), nullable=False) # Wikimedia username
    is_active = db.Column(db.Boolean, default=True)
    closed_at = db.Column(db.DateTime, nullable=True)

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    data = db.Column(db.Text, nullable=False) # JSON string of submitted data
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_by = db.Column(db.String(255), nullable=True) # Optional Wikimedia username
    # Confirmed Wikimedia email, stored only for forms that opt in to collecting it
    submitted_email = db.Column(db.String(255), nullable=True)

class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    username = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False) # 'admin', 'viewer'

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    details = db.Column(db.Text, nullable=True)


class GoogleAccount(db.Model):
    """One Google account per Wikimedia user, for Sheets sync.

    Only the encrypted refresh token is stored. The access token is held
    in memory only for the request/worker run that needs it. ``google_email``
    is display-only and is NULL in v1: the implementation does not fetch a
    Google profile, so no identity scope is requested (see google_auth.py).
    """
    id = db.Column(db.Integer, primary_key=True)
    wikimedia_username = db.Column(db.String(255), nullable=False, unique=True)
    google_email = db.Column(db.String(255), nullable=True)
    encrypted_refresh_token = db.Column(db.Text, nullable=False)
    scopes = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SheetSync(db.Model):
    """One spreadsheet per form (v1). ``columns_json`` is the frozen header
    mapping (meta columns + field keys, in order); it is only appended to,
    never reordered, when a form gains a question."""
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False, unique=True)
    spreadsheet_id = db.Column(db.String(255), nullable=False)
    spreadsheet_url = db.Column(db.String(512), nullable=True)
    sheet_title = db.Column(db.String(255), nullable=False, default='Responses')
    # 'active' | 'paused' | 'error' | 'disconnected'
    status = db.Column(db.String(50), nullable=False, default='active')
    last_error = db.Column(db.Text, nullable=True)  # short kind + message, no tokens
    header_version = db.Column(db.Integer, nullable=False, default=1)
    columns_json = db.Column(db.Text, nullable=False, default='[]')
    last_synced_at = db.Column(db.DateTime, nullable=True)
    last_synced_submission_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.String(255), nullable=False)  # Wikimedia username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncQueue(db.Model):
    """One queue row per submission, at most. Re-queue a sent/failed row by
    updating it back to 'pending', never by inserting a duplicate.

    Statuses: 'pending' | 'sending' | 'sent' | 'failed' | 'skipped'.
    'sending' rows are claimed by the worker; stale ones (lease expired)
    are reclaimed to 'pending' by the worker (see sync_worker.py).
    """
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    submission_id = db.Column(
        db.Integer, db.ForeignKey('submission.id'), nullable=False, unique=True)
    status = db.Column(db.String(50), nullable=False, default='pending')
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    next_retry_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('submission_id', name='uq_syncqueue_submission'),
    )


def enqueue_submission(form_id, submission_id):
    """Create or re-queue a SyncQueue row for a submission.

    Returns the row, or ``None`` if the form has no active sync. Never
    raises for a duplicate: the unique constraint on ``submission_id``
    is honoured by updating the existing row back to 'pending'.
    """
    sync = SheetSync.query.filter_by(form_id=form_id, status='active').first()
    if not sync:
        return None
    row = SyncQueue.query.filter_by(submission_id=submission_id).first()
    if row is None:
        row = SyncQueue(form_id=form_id, submission_id=submission_id)
        db.session.add(row)
    else:
        row.form_id = form_id
        row.status = 'pending'
        row.next_retry_at = None
        row.last_error = None
    return row


def _column_names(table):
    """Current column names of ``table`` (fresh inspection, no cached metadata)."""
    return {c['name'] for c in db.inspect(db.engine).get_columns(table)}


def _add_column_if_missing(table, column, ddl):
    """Add a column idempotently, tolerating another worker adding it concurrently.

    Several uWSGI/Gunicorn workers may start at once and all see the column
    missing. Only one ALTER can win; the others fail with a duplicate-column
    error, so on failure we re-check and only re-raise if the column is still
    absent (i.e. the error was something else).
    """
    if column in _column_names(table):
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(db.text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
    except (OperationalError, ProgrammingError, InternalError):
        if column not in _column_names(table):
            raise


def ensure_schema_upgrades():
    """Add columns introduced after a table was first created.

    ``db.create_all()`` creates missing tables but never alters existing ones,
    so the production database needs new nullable columns added explicitly.
    Safe to run from every worker on startup.

    NOTE (Google Sheets sync): the new ``google_account``, ``sheet_sync``
    and ``sync_queue`` tables are created by ``db.create_all()`` in
    ``app.py`` on startup — this function is NOT a substitute for that.
    Verify startup against an existing SQLite/MariaDB database when
    deploying, and only use this helper for nullable columns added later.
    """
    if 'submission' not in db.inspect(db.engine).get_table_names():
        return
    _add_column_if_missing('submission', 'submitted_email', 'VARCHAR(255) NULL')
