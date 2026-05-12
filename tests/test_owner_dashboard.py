from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from config import Config
from models import db, Form, Submission


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


def set_username(client, username):
    with client.session_transaction() as session:
        session['username'] = username


def seed_forms():
    active_form = Form(
        title='Owner Survey',
        schema='{}',
        created_by='MdsShakil',
        is_active=True
    )
    closed_form = Form(
        title='Team Feedback',
        schema='{}',
        created_by='AnotherUser',
        is_active=False,
        closed_at=datetime.now(tz=timezone.utc) - timedelta(days=2)
    )
    db.session.add_all([active_form, closed_form])
    db.session.commit()

    db.session.add_all([
        Submission(form_id=active_form.id, data='{"a": 1}', submitted_by='User1'),
        Submission(form_id=active_form.id, data='{"a": 2}', submitted_by='User2'),
        Submission(form_id=closed_form.id, data='{"b": 1}', submitted_by='User3'),
    ])
    db.session.commit()


def test_owner_dashboard_denies_non_owner(client):
    set_username(client, 'RegularUser')
    response = client.get('/owner/dashboard', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


def test_owner_dashboard_shows_table_and_filters(app, client):
    with app.app_context():
        seed_forms()

    set_username(client, 'MdsShakil')
    response = client.get('/owner/dashboard')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Owner%20Survey' in html
    assert 'AnotherUser' in html
    assert '>2<' in html

    filtered = client.get('/owner/dashboard?creator=AnotherUser&status=closed')
    filtered_html = filtered.get_data(as_text=True)
    assert filtered.status_code == 200
    assert 'Team%20Feedback' in filtered_html
    assert 'Owner%20Survey' not in filtered_html
