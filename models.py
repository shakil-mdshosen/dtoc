from flask_sqlalchemy import SQLAlchemy
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


def ensure_schema_upgrades():
    """Add columns introduced after a table was first created.

    ``db.create_all()`` creates missing tables but never alters existing ones,
    so the production database needs new nullable columns added explicitly.
    """
    inspector = db.inspect(db.engine)
    if 'submission' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('submission')}
    if 'submitted_email' not in columns:
        with db.engine.begin() as conn:
            conn.execute(db.text('ALTER TABLE submission ADD COLUMN submitted_email VARCHAR(255) NULL'))
