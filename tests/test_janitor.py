import pytest
from datetime import datetime, timedelta
from app import create_app
from models import db, Form, Submission, AuditLog
from config import Config
from janitor import run_janitor

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False

@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

def test_janitor_purges_old_submissions(app):
    with app.app_context():
        # Create a closed form older than 90 days
        old_form = Form(
            title="Old Form",
            schema="{}",
            created_by="TestUser",
            is_active=False,
            closed_at=datetime.utcnow() - timedelta(days=95)
        )
        db.session.add(old_form)
        db.session.commit()

        # Add submissions to the old form
        sub1 = Submission(form_id=old_form.id, data='{"field": "val1"}', submitted_by="User1")
        sub2 = Submission(form_id=old_form.id, data='{"field": "val2"}', submitted_by="User2")
        db.session.add_all([sub1, sub2])
        db.session.commit()

        # Create a closed form newer than 90 days
        new_form = Form(
            title="New Form",
            schema="{}",
            created_by="TestUser",
            is_active=False,
            closed_at=datetime.utcnow() - timedelta(days=85)
        )
        db.session.add(new_form)
        db.session.commit()

        # Add submissions to the new form
        sub3 = Submission(form_id=new_form.id, data='{"field": "val3"}', submitted_by="User3")
        db.session.add(sub3)
        db.session.commit()

        # Create an active form
        active_form = Form(
            title="Active Form",
            schema="{}",
            created_by="TestUser",
            is_active=True
        )
        db.session.add(active_form)
        db.session.commit()

        # Add submissions to active form
        sub4 = Submission(form_id=active_form.id, data='{"field": "val4"}', submitted_by="User4")
        db.session.add(sub4)
        db.session.commit()

        # Run the janitor
        run_janitor(app)

        # Verify old submissions are deleted
        old_submissions = Submission.query.filter_by(form_id=old_form.id).all()
        assert len(old_submissions) == 0

        # Verify old form still exists
        assert Form.query.get(old_form.id) is not None

        # Verify new submissions still exist
        new_submissions = Submission.query.filter_by(form_id=new_form.id).all()
        assert len(new_submissions) == 1

        # Verify active submissions still exist
        active_submissions = Submission.query.filter_by(form_id=active_form.id).all()
        assert len(active_submissions) == 1

        # Verify Audit log was created
        logs = AuditLog.query.filter_by(action='DATA_PURGE').all()
        assert len(logs) == 1
        assert str(old_form.id) in logs[0].details
