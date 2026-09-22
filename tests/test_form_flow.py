import json

import pytest

from app import create_app
from config import Config
from models import db, Form, Submission, Permission


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


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
    client = app.test_client()
    with client.session_transaction() as session:
        session['username'] = 'Alice'
    return client


SCHEMA = {
    'description_html': '<p>Hello <script>alert(1)</script><b>world</b></p>',
    'settings': {'theme_color': 'teal', 'one_response_per_user': True},
    'fields': [
        {'id': 'q1', 'type': 'text', 'label': 'Name', 'required': True},
        {'id': 'q2', 'type': 'rating', 'label': 'Rating', 'max': 5},
    ],
}


def create_form(client, schema=SCHEMA, title='Survey'):
    response = client.post('/builder', data={'title': title, 'schema': json.dumps(schema)})
    assert response.status_code == 302
    return Form.query.order_by(Form.id.desc()).first()


def test_builder_creates_sanitized_form_and_redirects_to_share(app, client):
    response = client.post('/builder', data={'title': 'Survey', 'schema': json.dumps(SCHEMA)})
    assert '/share' in response.headers['Location']
    form = Form.query.first()
    stored = json.loads(form.schema)
    assert '<script>' not in stored['description_html']
    assert stored['settings']['theme_color'] == 'teal'
    assert stored['fields'][0]['key'] == 'Name'
    assert Permission.query.filter_by(form_id=form.id, username='Alice', role='admin').first()


def test_builder_requires_title(client):
    response = client.post('/builder', data={'title': '  ', 'schema': json.dumps(SCHEMA)})
    assert response.status_code == 400


def test_submit_validates_and_stores_by_label(app, client):
    form = create_form(client)
    bad = client.post(f'/form/{form.id}', json={'answers': {'q2': 4}})
    assert bad.status_code == 400
    assert 'q1' in bad.get_json()['errors']

    ok = client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice', 'q2': 4}})
    assert ok.status_code == 201
    sub = Submission.query.one()
    assert json.loads(sub.data) == {'Name': 'Alice', 'Rating': 4}


def test_one_response_per_user(app, client):
    form = create_form(client)
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})
    again = client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})
    assert again.status_code == 409
    page = client.get(f'/form/{form.id}')
    assert "already responded" in page.get_data(as_text=True)


def test_response_limit_and_close_date(app, client):
    schema = dict(SCHEMA, settings={'response_limit': 1})
    form = create_form(client, schema)
    assert client.post(f'/form/{form.id}', json={'answers': {'q1': 'A'}}).status_code == 201
    assert client.post(f'/form/{form.id}', json={'answers': {'q1': 'B'}}).status_code == 409

    past = dict(SCHEMA, settings={'close_at': '2000-01-01T00:00'})
    closed = create_form(client, past, title='Old')
    page = client.get(f'/form/{closed.id}')
    assert 'This form is closed' in page.get_data(as_text=True)
    assert db.session.get(Form, closed.id).is_active is False


def test_form_view_embeds_schema_safely(app, client):
    schema = dict(SCHEMA, fields=[{'id': 'q1', 'type': 'text', 'label': '</script><script>alert(1)</script>'}])
    form = create_form(client, schema)
    html = client.get(f'/form/{form.id}').get_data(as_text=True)
    assert '</script><script>alert(1)' not in html


def test_results_page_shows_summary_and_rows(app, client):
    form = create_form(client)
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice', 'q2': 5}})
    html = client.get(f'/form/{form.id}/submissions').get_data(as_text=True)
    assert 'Responses over time' not in html  # only one day of data
    assert 'Rating' in html and 'Alice' in html


def test_duplicate_and_delete_form(app, client):
    form = create_form(client)
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})
    response = client.post(f'/form/{form.id}/duplicate')
    assert response.status_code == 302
    copy = Form.query.filter(Form.id != form.id).one()
    assert copy.title == 'Copy of Survey'

    client.post(f'/form/{form.id}/delete')
    assert db.session.get(Form, form.id) is None
    assert Submission.query.filter_by(form_id=form.id).count() == 0


def test_non_admin_cannot_delete(app, client):
    form = create_form(client)
    with client.session_transaction() as session:
        session['username'] = 'Mallory'
    client.post(f'/form/{form.id}/delete')
    assert db.session.get(Form, form.id) is not None


def test_csv_export_uses_form_order(app, client):
    form = create_form(client)
    client.post(f'/form/{form.id}', json={'answers': {'q2': 3, 'q1': 'Bob'}})
    csv_text = client.get(f'/form/{form.id}/export/csv').get_data(as_text=True)
    header = csv_text.splitlines()[0]
    assert header == 'ID,Submitted At,Submitted By,Name,Rating'


def test_dashboard_and_pages_render(app, client):
    form = create_form(client)
    for url in ['/', '/dashboard', '/builder', f'/form/{form.id}/edit', f'/form/{form.id}/share', f'/form/{form.id}']:
        assert client.get(url).status_code == 200, url
