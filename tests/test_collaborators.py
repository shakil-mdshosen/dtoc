import json

import pytest

import forms_api
import wiki_users
from app import create_app
from config import Config
from models import db, Form, Permission
from wiki_users import LookupUnavailable, normalize_username


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    OWNER_USERNAME = 'ToolOwner'


EXISTING = {'Bob', 'Carol', 'Bob Smith'}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(forms_api, 'user_exists', lambda name: name in EXISTING)
    monkeypatch.setattr(forms_api, 'search_users',
                        lambda q: sorted(n for n in EXISTING if n.startswith(normalize_username(q))))
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def as_user(client, username):
    with client.session_transaction() as session:
        session['username'] = username
    return client


@pytest.fixture
def owner(app):
    client = as_user(app.test_client(), 'Alice')
    schema = {'fields': [{'id': 'q1', 'type': 'text', 'label': 'Name'}]}
    client.post('/builder', data={'title': 'Survey', 'schema': json.dumps(schema)})
    return client


def add(client, username, role=None):
    body = {'username': username}
    if role:
        body['role'] = role
    return client.post('/api/form/1/collaborator', json=body)


def test_owner_adds_viewer_by_default_and_editor_on_request(owner):
    assert add(owner, 'Bob').get_json()['role'] == 'viewer'
    assert add(owner, 'Carol', 'editor').get_json()['role'] == 'editor'
    roles = {p.username: p.role for p in Permission.query.filter_by(form_id=1)}
    assert roles == {'Alice': 'admin', 'Bob': 'viewer', 'Carol': 'editor'}


def test_usernames_are_normalised_and_must_exist(owner):
    assert add(owner, 'bob_Smith').get_json()['username'] == 'Bob Smith'
    missing = add(owner, 'Nobody')
    assert missing.status_code == 404
    assert 'No Wikimedia account' in missing.get_json()['error']
    assert add(owner, 'Bob Smith').status_code == 400  # duplicate after normalisation
    assert add(owner, 'Bob', 'admin').status_code == 400  # can't grant owner


def test_meta_outage_does_not_block_adding(owner, monkeypatch):
    def down(name):
        raise LookupUnavailable('timeout')
    monkeypatch.setattr(forms_api, 'user_exists', down)
    response = add(owner, 'Dave')
    assert response.status_code == 200
    assert response.get_json()['verified'] is False


def test_editor_can_edit_and_view_but_not_manage(app, owner):
    add(owner, 'Carol', 'editor')
    carol = as_user(app.test_client(), 'Carol')

    assert carol.get('/form/1/edit').status_code == 200
    schema = {'fields': [{'id': 'q1', 'type': 'text', 'label': 'Full name'}]}
    carol.post('/form/1/edit', data={'title': 'Survey v2', 'schema': json.dumps(schema)})
    assert db.session.get(Form, 1).title == 'Survey v2'
    assert carol.get('/form/1/submissions').status_code == 200
    assert carol.get('/form/1/export/csv').status_code == 200

    assert add(carol, 'Bob').status_code == 403
    carol.post('/form/1/close')
    assert db.session.get(Form, 1).is_active is True
    carol.post('/form/1/delete')
    assert db.session.get(Form, 1) is not None


def test_viewer_cannot_edit(app, owner):
    add(owner, 'Bob')
    bob = as_user(app.test_client(), 'Bob')
    assert bob.get('/form/1/submissions').status_code == 200
    response = bob.get('/form/1/edit')
    assert response.status_code == 302
    bob.post('/form/1/edit', data={'title': 'Hacked', 'schema': '{}'})
    assert db.session.get(Form, 1).title == 'Survey'


def test_owner_switches_roles(app, owner):
    add(owner, 'Bob')
    assert owner.patch('/api/form/1/collaborator/Bob', json={'role': 'editor'}).status_code == 200
    bob = as_user(app.test_client(), 'Bob')
    assert bob.get('/form/1/edit').status_code == 200

    owner.patch('/api/form/1/collaborator/Bob', json={'role': 'viewer'})
    assert bob.get('/form/1/edit').status_code == 302

    assert owner.patch('/api/form/1/collaborator/Bob', json={'role': 'admin'}).status_code == 400
    assert owner.patch('/api/form/1/collaborator/Alice', json={'role': 'viewer'}).status_code == 400
    assert owner.patch('/api/form/1/collaborator/Nobody', json={'role': 'viewer'}).status_code == 404
    assert bob.patch('/api/form/1/collaborator/Bob', json={'role': 'editor'}).status_code == 403


def test_owner_removes_collaborators_but_not_themself(owner):
    add(owner, 'Bob', 'editor')
    assert owner.delete('/api/form/1/collaborator/Bob').status_code == 200
    assert owner.delete('/api/form/1/collaborator/Alice').status_code == 400
    assert Permission.query.filter_by(form_id=1).count() == 1


def test_results_page_shows_role_controls_to_owner(owner):
    add(owner, 'Bob', 'editor')
    html = owner.get('/form/1/submissions').get_data(as_text=True)
    assert 'data-role-for="Bob"' in html
    assert '<option value="editor" selected>Editor</option>' in html


def test_dashboard_labels_and_edit_link_for_editor(app, owner):
    add(owner, 'Carol', 'editor')
    html = as_user(app.test_client(), 'Carol').get('/dashboard').get_data(as_text=True)
    assert '>Editor</span>' in html
    assert '/form/1/edit' in html


def test_user_search_endpoint(app, owner):
    assert owner.get('/api/users/search?q=bo').get_json() == {'users': ['Bob', 'Bob Smith']}
    anonymous = app.test_client().get('/api/users/search?q=bo')
    assert anonymous.status_code == 401


def test_user_search_reports_outage(owner, monkeypatch):
    def down(q):
        raise LookupUnavailable('down')
    monkeypatch.setattr(forms_api, 'search_users', down)
    response = owner.get('/api/users/search?q=bo')
    assert response.status_code == 503
    assert response.get_json()['users'] == []


# --- wiki_users module against a fake Meta-Wiki API ---

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.mark.parametrize('raw, expected', [
    ('bob_smith', 'Bob smith'), ('bob_Smith', 'Bob Smith'), ('  User:alice  ', 'Alice'), ('a  b', 'A b'), ('', ''), (None, ''),
])
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


def test_search_users_filters_locked_and_short_queries(monkeypatch):
    wiki_users._cache.clear()
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(params)
        return FakeResponse({'query': {'globalallusers': [
            {'id': 1, 'name': 'Bob'}, {'id': 2, 'name': 'Bobby', 'locked': True}, {'id': 3, 'name': 'Bobcat'},
        ]}})

    monkeypatch.setattr(wiki_users.requests, 'get', fake_get)
    assert wiki_users.search_users('b') == []
    assert wiki_users.search_users('bo') == ['Bob', 'Bobcat']
    assert calls[0]['list'] == 'globalallusers' and calls[0]['aguprefix'] == 'Bo'
    wiki_users.search_users('bo')
    assert len(calls) == 1  # cached


@pytest.mark.parametrize('payload, expected', [
    ({'globaluserinfo': {'home': 'enwiki', 'id': 5, 'name': 'Bob'}}, True),
    ({'globaluserinfo': {'missing': True}}, False),
    ({'globaluserinfo': {'missing': ''}}, False),
    ({'globaluserinfo': {'id': 5, 'name': 'Bob', 'locked': True}}, False),
])
def test_user_exists(monkeypatch, payload, expected):
    wiki_users._cache.clear()
    monkeypatch.setattr(wiki_users.requests, 'get', lambda *a, **k: FakeResponse({'query': payload}))
    assert wiki_users.user_exists('Bob') is expected


def test_lookup_errors_raise_unavailable(monkeypatch):
    wiki_users._cache.clear()

    def boom(*a, **k):
        raise wiki_users.requests.ConnectionError('no route')
    monkeypatch.setattr(wiki_users.requests, 'get', boom)
    with pytest.raises(LookupUnavailable):
        wiki_users.user_exists('Bob')
