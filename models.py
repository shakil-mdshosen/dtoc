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
    """
    if 'submission' not in db.inspect(db.engine).get_table_names():
        return
    _add_column_if_missing('submission', 'submitted_email', 'VARCHAR(255) NULL')
