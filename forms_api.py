from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, flash, current_app
from models import db, Form, Submission, Permission, AuditLog
import json
import base64
import binascii
import re
from functools import wraps
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from datetime import datetime

from wiki_users import LookupUnavailable, normalize_username, search_users, user_exists
from form_schema import (
    prepare_schema, validate_submission, sanitize_fields, sanitize_settings,
    summarize, column_headers, format_value, input_fields,
)

forms_bp = Blueprint('forms', __name__)
HEADER_IMAGE_MAX_BYTES = 2 * 1024 * 1024
DATA_IMAGE_PATTERN = re.compile(
    r'^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)$',
    re.IGNORECASE
)

ALLOWED_DESCRIPTION_TAGS = {
    'b', 'strong', 'i', 'em', 'u', 's', 'strike',
    'br', 'p', 'div', 'ul', 'ol', 'li', 'blockquote', 'a'
}


class DescriptionHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.open_tags = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_DESCRIPTION_TAGS:
            self.open_tags.append(None)
            return
        if tag == 'br':
            self.parts.append('<br>')
            self.open_tags.append(None)
            return
        if tag == 'a':
            href = dict(attrs).get('href', '')
            safe_href = sanitize_link_url(href)
            if safe_href:
                self.parts.append(
                    f'<a href="{escape(safe_href, quote=True)}" target="_blank" rel="noopener noreferrer nofollow">'
                )
                self.open_tags.append('a')
                return
            self.open_tags.append(None)
            return
        self.parts.append(f'<{tag}>')
        self.open_tags.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        opened = self.open_tags.pop() if self.open_tags else None
        if opened == tag and tag != 'br':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(escape(data))

    def get_html(self):
        return ''.join(self.parts).strip()


class PlainTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return ''.join(self.parts).strip()


def sanitize_link_url(url):
    if not isinstance(url, str):
        return ''
    value = url.strip()
    if not value:
        return ''
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in {'http', 'https'} and parsed.netloc:
        return value
    if scheme == 'mailto' and parsed.path:
        return value
    return ''


def sanitize_header_image_url(url):
    if not isinstance(url, str):
        return ''
    value = url.strip()
    if not value:
        return ''
    data_match = DATA_IMAGE_PATTERN.match(value)
    if data_match:
        encoded = data_match.group(2)
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return ''
        if len(decoded) <= HEADER_IMAGE_MAX_BYTES:
            return value
        return ''
    parsed = urlparse(value)
    if parsed.scheme.lower() in {'http', 'https'} and parsed.netloc:
        return value
    return ''


def sanitize_description_html(raw_html):
    if not isinstance(raw_html, str):
        return ''
    sanitizer = DescriptionHTMLSanitizer()
    sanitizer.feed(raw_html)
    sanitizer.close()
    return sanitizer.get_html()


def to_plain_text(raw_html):
    if not isinstance(raw_html, str):
        return ''
    extractor = PlainTextExtractor()
    extractor.feed(raw_html)
    extractor.close()
    return extractor.get_text()


def sanitize_schema(raw_schema):
    if not isinstance(raw_schema, dict):
        raw_schema = {}
    schema = dict(raw_schema)
    raw_description = schema.get('description_html') or schema.get('description') or ''
    description_html = sanitize_description_html(raw_description)
    schema['description_html'] = description_html
    schema['description'] = to_plain_text(description_html)
    header_image_url = sanitize_header_image_url(schema.get('header_image_url', ''))
    if header_image_url:
        schema['header_image_url'] = header_image_url
    else:
        schema.pop('header_image_url', None)
    schema['fields'] = sanitize_fields(schema.get('fields'))
    schema['settings'] = sanitize_settings(schema.get('settings'))
    allowed = {'description_html', 'description', 'header_image_url', 'fields', 'settings'}
    return {k: v for k, v in schema.items() if k in allowed}


def load_schema(form):
    try:
        raw = json.loads(form.schema) if form.schema else {}
    except (TypeError, ValueError):
        raw = {}
    return prepare_schema(raw)


def load_data(submission):
    try:
        data = json.loads(submission.data)
    except (TypeError, ValueError):
        return {}
    if isinstance(data, dict) and isinstance(data.get('data'), dict):
        data = data['data']
    return data if isinstance(data, dict) else {}


def get_permission(form_id, admin=False):
    query = Permission.query.filter_by(form_id=form_id, username=session.get('username'))
    if admin:
        query = query.filter_by(role='admin')
    return query.first()


def can_manage(form, admin=False):
    return bool(get_permission(form.id, admin=admin)) or is_owner_user(session.get('username'))


# Collaborator roles. 'admin' is the form's owner (its creator); owners can add,
# remove and switch 'editor' and 'viewer' collaborators.
COLLABORATOR_ROLES = ('editor', 'viewer')
ROLE_LABELS = {'admin': 'Owner', 'editor': 'Editor', 'viewer': 'Viewer'}


def can_edit(form):
    """Owners and editors can change the form's questions and settings."""
    perm = get_permission(form.id)
    return bool(perm and perm.role in ('admin', 'editor')) or is_owner_user(session.get('username'))


def enforce_close_date(form, schema):
    """Close a form automatically once its scheduled close date has passed."""
    close_at = schema['settings'].get('close_at')
    if not (form.is_active and close_at):
        return
    try:
        deadline = datetime.fromisoformat(close_at)
    except ValueError:
        return
    if datetime.utcnow() >= deadline:
        form.is_active = False
        form.closed_at = deadline
        db.session.add(AuditLog(action='AUTO_CLOSE_FORM', details=f"Form {form.id} closed on schedule"))
        db.session.commit()


def availability(form, schema):
    """Return a reason string when the form can't take a response from this user."""
    enforce_close_date(form, schema)
    if not form.is_active:
        return 'closed'
    settings = schema['settings']
    limit = settings.get('response_limit')
    if limit and Submission.query.filter_by(form_id=form.id).count() >= limit:
        return 'limit'
    if settings.get('one_response_per_user') and Submission.query.filter_by(
            form_id=form.id, submitted_by=session.get('username')).first():
        return 'responded'
    if settings.get('collect_email') and not session.get('email'):
        return 'email_required'
    return None


def escape_like_pattern(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def is_owner_user(username):
    owner_username = (current_app.config.get('OWNER_USERNAME') or '').strip()
    current_user = (username or '').strip()
    return bool(owner_username) and current_user.casefold() == owner_username.casefold()


def _referrer_path():
    """Path of the page that sent this request, if it was a page on this site."""
    referrer = urlparse(request.referrer or '')
    if referrer.netloc != request.host:
        return None
    return referrer.path + (f'?{referrer.query}' if referrer.query else '')


def _json_return_path():
    """Where to send someone after re-login when a background request found them logged out."""
    # A form submission is POSTed to the form's own URL, so return there directly;
    # this works even when the browser doesn't send a Referer header.
    if request.endpoint == 'forms.view_form':
        return request.path
    return _referrer_path() or url_for('forms.dashboard')


def login_required_response():
    """Ask an anonymous visitor to log in, remembering where they wanted to go."""
    from auth import login_url, safe_next_url
    if request.method == 'GET':
        return render_template(
            'login_required.html',
            login_link=login_url(),
            is_form=request.endpoint == 'forms.view_form',
        ), 401
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({"status": "error", "error": "Login required",
                        "message": "Your session has expired. Please log in again.",
                        "login_url": login_url(_json_return_path())}), 401
    # A form POST (e.g. close/delete): send them back to the page they were on after login.
    back = safe_next_url(_referrer_path())
    flash("Please log in to continue.", "warning")
    return redirect(login_url(back or url_for('forms.dashboard')))


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return login_required_response()
        return f(*args, **kwargs)
    return decorated_function


def owner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_owner_user(session.get('username')):
            flash("You don't have permission to access the owner dashboard.", "danger")
            return redirect(url_for('forms.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def _save_form_from_request(form=None):
    """Create or update a form from the builder POST. Returns (form, error)."""
    title = (request.form.get('title') or '').strip()[:255]
    raw_schema = request.form.get('schema')
    try:
        parsed_schema = json.loads(raw_schema) if raw_schema else {}
    except json.JSONDecodeError:
        return None, "Invalid form data. Please try again."
    if not title:
        return None, "Please give your form a title."
    clean_schema = sanitize_schema(parsed_schema)
    if form is None:
        form = Form(title=title, schema=json.dumps(clean_schema), created_by=session['username'])
        db.session.add(form)
    else:
        form.title = title
        form.schema = json.dumps(clean_schema)
    return form, None


@forms_bp.route('/builder', methods=['GET', 'POST'])
@login_required
def builder():
    if request.method == 'POST':
        new_form, error = _save_form_from_request()
        if error:
            flash(error, "danger")
            return render_template('builder.html', schema_json=request.form.get('schema') or '{}',
                                   draft_title=request.form.get('title', '')), 400
        db.session.commit()

        db.session.add(Permission(form_id=new_form.id, username=session['username'], role='admin'))
        db.session.add(AuditLog(action='CREATE_FORM', details=f"Form {new_form.id} created by {session['username']}"))
        db.session.commit()

        flash("Form created! Share the link below to start collecting responses.", "success")
        return redirect(url_for('forms.form_share', form_id=new_form.id))

    return render_template('builder.html')


@forms_bp.route('/form/<int:form_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_edit(form):
        flash("You don't have permission to edit this form.", "danger")
        return redirect(url_for('forms.dashboard'))

    if request.method == 'POST':
        _, error = _save_form_from_request(form)
        if error:
            db.session.rollback()
            flash(error, "danger")
            return render_template('builder.html', existing_form=form,
                                   schema_json=json.dumps(load_schema(form))), 400
        db.session.add(AuditLog(action='EDIT_FORM', details=f"Form {form.id} edited by {session['username']}"))
        db.session.commit()
        flash("Form updated successfully!", "success")
        return redirect(url_for('forms.dashboard'))

    return render_template('builder.html', existing_form=form, schema_json=json.dumps(load_schema(form)))


@forms_bp.route('/form/<int:form_id>/share')
@login_required
def form_share(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_manage(form):
        flash("You don't have permission to manage this form.", "danger")
        return redirect(url_for('forms.dashboard'))
    return render_template('form_share.html', form=form,
                           form_url=url_for('forms.view_form', form_id=form.id, _external=True))


@forms_bp.route('/form/<int:form_id>', methods=['GET', 'POST'])
@login_required
def view_form(form_id):
    form = Form.query.get_or_404(form_id)
    schema = load_schema(form)
    reason = availability(form, schema)

    if request.method == 'POST':
        if reason:
            messages = {
                'closed': "This form is closed.",
                'limit': "This form has reached its response limit.",
                'responded': "You have already responded to this form.",
                'email_required': "This form requires a confirmed email address on your Wikimedia account.",
            }
            return jsonify({"status": "error", "message": messages[reason]}), 409
        payload = request.get_json(silent=True) or {}
        answers = payload.get('answers') if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return jsonify({"status": "error", "message": "Invalid submission."}), 400
        data, errors = validate_submission(schema, answers)
        if errors:
            return jsonify({"status": "error", "message": "Please fix the highlighted answers.",
                            "errors": errors}), 400
        submission = Submission(
            form_id=form.id,
            data=json.dumps(data),
            submitted_by=session.get('username'),
            submitted_email=session.get('email') if schema['settings'].get('collect_email') else None,
        )
        db.session.add(submission)
        db.session.commit()
        return jsonify({"status": "success", "message": "Submission received"}), 201

    if reason == 'email_required':
        from auth import login_url
        return render_template('email_required.html', form=form,
                               recheck_url=login_url(request.path),
                               checked=session.get('email_checked', False)), 403
    if reason:
        return render_template('form_closed.html', form=form, reason=reason)
    permissions = Permission.query.filter_by(form_id=form.id).all()
    return render_template('form_view.html', form=form, schema=schema, permissions=permissions,
                           can_edit=can_edit(form), role_labels=ROLE_LABELS)


def _response_counts(form_ids):
    if not form_ids:
        return {}
    rows = (
        db.session.query(Submission.form_id, db.func.count(Submission.id), db.func.max(Submission.submitted_at))
        .filter(Submission.form_id.in_(form_ids))
        .group_by(Submission.form_id)
        .all()
    )
    return {form_id: {'count': count, 'last': last} for form_id, count, last in rows}


@forms_bp.route('/dashboard')
@login_required
def dashboard():
    perms = Permission.query.filter_by(username=session['username']).all()
    roles = {p.form_id: p.role for p in perms}
    forms = (Form.query.filter(Form.id.in_(list(roles))).order_by(Form.created_at.desc()).all()
             if roles else [])
    stats = _response_counts([f.id for f in forms])
    totals = {
        'forms': len(forms),
        'active': sum(1 for f in forms if f.is_active),
        'responses': sum(s['count'] for s in stats.values()),
    }
    return render_template('dashboard.html', forms=forms, roles=roles, stats=stats, totals=totals)


@forms_bp.route('/owner/dashboard')
@login_required
@owner_required
def owner_dashboard():
    creator_filter = (request.args.get('creator') or '').strip()
    title_filter = (request.args.get('title') or '').strip()
    status_filter = (request.args.get('status') or 'all').strip().lower()
    if status_filter not in {'all', 'active', 'closed'}:
        status_filter = 'all'

    query = (
        db.session.query(Form, db.func.count(Submission.id).label('total_responses'))
        .outerjoin(Submission, Submission.form_id == Form.id)
    )

    if creator_filter:
        creator_pattern = '%' + escape_like_pattern(creator_filter) + '%'
        query = query.filter(Form.created_by.ilike(creator_pattern, escape='\\'))
    if title_filter:
        title_pattern = '%' + escape_like_pattern(title_filter) + '%'
        query = query.filter(Form.title.ilike(title_pattern, escape='\\'))
    if status_filter == 'active':
        query = query.filter(Form.is_active.is_(True))
    elif status_filter == 'closed':
        query = query.filter(Form.is_active.is_(False))

    records = (
        query.group_by(Form.id)
        .order_by(Form.created_at.desc())
        .all()
    )
    rows = [
        {
            'creator': form.created_by,
            'title': form.title,
            'created_at': form.created_at,
            'closed_at': form.closed_at,
            'is_active': form.is_active,
            'total_responses': total_responses,
            'form_url': url_for('forms.view_form', form_id=form.id, _external=True),
            'form_id': form.id,
        }
        for form, total_responses in records
    ]
    totals = {
        'forms': len(rows),
        'active': sum(1 for r in rows if r['is_active']),
        'responses': sum(r['total_responses'] for r in rows),
        'creators': len({r['creator'] for r in rows}),
    }

    return render_template(
        'owner_dashboard.html',
        rows=rows,
        totals=totals,
        creator_filter=creator_filter,
        title_filter=title_filter,
        status_filter=status_filter
    )


@forms_bp.route('/form/<int:form_id>/close', methods=['POST'])
@login_required
def close_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_manage(form, admin=True):
        flash("You don't have permission to close this form.", "danger")
        return redirect(url_for('forms.dashboard'))

    form.is_active = False
    form.closed_at = datetime.utcnow()
    db.session.add(AuditLog(action='CLOSE_FORM', details=f"Form {form.id} closed by {session['username']}"))
    db.session.commit()

    flash("Form closed. Data will be retained for 90 days.", "info")
    return redirect(request.referrer or url_for('forms.dashboard'))


@forms_bp.route('/form/<int:form_id>/reopen', methods=['POST'])
@login_required
def reopen_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_manage(form, admin=True):
        flash("You don't have permission to reopen this form.", "danger")
        return redirect(url_for('forms.dashboard'))

    form.is_active = True
    form.closed_at = None
    # A past schedule would immediately close the form again, so clear it.
    schema = load_schema(form)
    close_at = schema['settings'].get('close_at')
    if close_at and datetime.fromisoformat(close_at) <= datetime.utcnow():
        schema['settings']['close_at'] = ''
        form.schema = json.dumps(schema)
    db.session.add(AuditLog(action='REOPEN_FORM', details=f"Form {form.id} reopened by {session['username']}"))
    db.session.commit()

    flash("Form successfully reopened.", "success")
    return redirect(request.referrer or url_for('forms.dashboard'))


@forms_bp.route('/form/<int:form_id>/duplicate', methods=['POST'])
@login_required
def duplicate_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_manage(form):
        flash("You don't have permission to duplicate this form.", "danger")
        return redirect(url_for('forms.dashboard'))

    copy = Form(title=f"Copy of {form.title}"[:255], schema=form.schema, created_by=session['username'])
    db.session.add(copy)
    db.session.commit()
    db.session.add(Permission(form_id=copy.id, username=session['username'], role='admin'))
    db.session.add(AuditLog(action='DUPLICATE_FORM',
                            details=f"Form {form.id} duplicated as {copy.id} by {session['username']}"))
    db.session.commit()
    flash("Form duplicated. You can now edit the copy.", "success")
    return redirect(url_for('forms.edit_form', form_id=copy.id))


@forms_bp.route('/form/<int:form_id>/delete', methods=['POST'])
@login_required
def delete_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not can_manage(form, admin=True):
        flash("You don't have permission to delete this form.", "danger")
        return redirect(url_for('forms.dashboard'))

    Submission.query.filter_by(form_id=form.id).delete()
    Permission.query.filter_by(form_id=form.id).delete()
    db.session.add(AuditLog(action='DELETE_FORM',
                            details=f"Form {form.id} ({form.title}) deleted by {session['username']}"))
    db.session.delete(form)
    db.session.commit()
    flash("Form and all of its responses were deleted.", "info")
    return redirect(url_for('forms.dashboard'))


@forms_bp.route('/form/<int:form_id>/submissions')
@login_required
def view_submissions(form_id):
    form = Form.query.get_or_404(form_id)
    perm = get_permission(form.id)
    if not perm:
        flash("You don't have permission to view these submissions.", "danger")
        return redirect(url_for('forms.dashboard'))

    schema = load_schema(form)
    submissions = Submission.query.filter_by(form_id=form.id).order_by(Submission.submitted_at.desc()).all()
    datas = [load_data(sub) for sub in submissions]
    headers = column_headers(schema, datas)
    rows = [
        {
            'id': sub.id,
            'submitted_at': sub.submitted_at,
            'submitted_by': sub.submitted_by,
            'submitted_email': sub.submitted_email,
            'cells': [format_value(data.get(h)) for h in headers],
        }
        for sub, data in zip(submissions, datas)
    ]
    daily = {}
    for sub in submissions:
        day = sub.submitted_at.strftime('%Y-%m-%d')
        daily[day] = daily.get(day, 0) + 1

    is_admin = perm.role == 'admin'
    collaborators = Permission.query.filter_by(form_id=form.id).all() if is_admin else []
    order = {'admin': 0, 'editor': 1, 'viewer': 2}
    collaborators.sort(key=lambda p: (order.get(p.role, 3), p.username.casefold()))

    return render_template(
        'submissions.html', form=form, schema=schema, submissions=submissions,
        headers=headers, rows=rows, summary=summarize(schema, datas),
        daily=sorted(daily.items())[-30:], question_count=len(input_fields(schema)),
        is_admin=is_admin, collaborators=collaborators,
        can_edit=can_edit(form), role=perm.role, role_labels=ROLE_LABELS,
        show_email=bool(schema['settings'].get('collect_email') or any(s.submitted_email for s in submissions)),
    )


@forms_bp.route('/api/form/<int:form_id>/submission/<int:sub_id>', methods=['DELETE'])
@login_required
def delete_submission(form_id, sub_id):
    if not get_permission(form_id):
        return jsonify({"error": "Unauthorized"}), 403

    sub = Submission.query.get_or_404(sub_id)
    if sub.form_id != form_id:
        return jsonify({"error": "Bad request"}), 400

    db.session.delete(sub)
    db.session.add(AuditLog(action='DELETE_SUBMISSION',
                            details=f"Submission {sub_id} from Form {form_id} deleted by {session['username']}"))
    db.session.commit()
    return jsonify({"status": "success"})


def _role_from(payload, default='viewer'):
    role = (payload.get('role') or default)
    return role if role in COLLABORATOR_ROLES else None


def _json_object():
    """The request's JSON body if it is an object, else ``None``."""
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _find_collaborator(form_id, username):
    """Permission for ``username`` on a form, also matching names stored before normalisation."""
    perm = Permission.query.filter_by(form_id=form_id, username=username).first()
    if perm:
        return perm
    wanted = normalize_username(username)
    return next((p for p in Permission.query.filter_by(form_id=form_id)
                 if normalize_username(p.username) == wanted), None)


@forms_bp.route('/api/users/search')
@login_required
def search_wiki_users():
    """Username suggestions from Meta-Wiki's global account list."""
    try:
        names = search_users(request.args.get('q', ''))
    except LookupUnavailable:
        return jsonify({"users": [], "error": "Suggestions are unavailable right now."}), 503
    return jsonify({"users": names})


@forms_bp.route('/api/form/<int:form_id>/collaborator', methods=['POST'])
@login_required
def add_collaborator(form_id):
    if not get_permission(form_id, admin=True):
        return jsonify({"error": "Only the form owner can add collaborators"}), 403

    payload = _json_object()
    if payload is None:
        return jsonify({"error": "Request body must be a JSON object"}), 400
    username = normalize_username(payload.get('username'))
    if not username:
        return jsonify({"error": "Username required"}), 400
    role = _role_from(payload)
    if not role:
        return jsonify({"error": "Role must be 'editor' or 'viewer'"}), 400

    if _find_collaborator(form_id, username):
        return jsonify({"error": f"{username} is already a collaborator"}), 400

    try:
        if not user_exists(username):
            return jsonify({"error": f"No Wikimedia account named \"{username}\" was found."}), 404
        verified = True
    except LookupUnavailable:
        # Don't block owners when Meta-Wiki is unreachable; the name is still normalised.
        verified = False

    db.session.add(Permission(form_id=form_id, username=username, role=role))
    db.session.add(AuditLog(action='ADD_COLLABORATOR',
                            details=f"Collaborator {username} ({role}) added to Form {form_id} by {session['username']}"))
    db.session.commit()

    return jsonify({"status": "success", "username": username, "role": role, "verified": verified})


@forms_bp.route('/api/form/<int:form_id>/collaborator/<username>', methods=['PATCH'])
@login_required
def change_collaborator_role(form_id, username):
    if not get_permission(form_id, admin=True):
        return jsonify({"error": "Only the form owner can change roles"}), 403

    payload = _json_object()
    if payload is None:
        return jsonify({"error": "Request body must be a JSON object"}), 400
    role = _role_from(payload, default='')
    if not role:
        return jsonify({"error": "Role must be 'editor' or 'viewer'"}), 400

    target_perm = _find_collaborator(form_id, username)
    if not target_perm:
        return jsonify({"error": "Collaborator not found"}), 404
    if target_perm.role == 'admin':
        return jsonify({"error": "The form owner's role can't be changed"}), 400

    if target_perm.role != role:
        old = target_perm.role
        target_perm.role = role
        db.session.add(AuditLog(action='CHANGE_COLLABORATOR_ROLE',
                                details=f"Collaborator {username} changed from {old} to {role} on Form {form_id} by {session['username']}"))
        db.session.commit()

    return jsonify({"status": "success", "username": username, "role": role})


@forms_bp.route('/api/form/<int:form_id>/collaborator/<username>', methods=['DELETE'])
@login_required
def remove_collaborator(form_id, username):
    if not get_permission(form_id, admin=True):
        return jsonify({"error": "Only the form owner can remove collaborators"}), 403

    target_perm = _find_collaborator(form_id, username)
    if not target_perm:
        return jsonify({"error": "Collaborator not found"}), 404

    if target_perm.role == 'admin':
        return jsonify({"error": "The form owner can't be removed"}), 400

    db.session.delete(target_perm)
    db.session.add(AuditLog(action='REMOVE_COLLABORATOR',
                            details=f"Collaborator {username} removed from Form {form_id} by {session['username']}"))
    db.session.commit()

    return jsonify({"status": "success"})
