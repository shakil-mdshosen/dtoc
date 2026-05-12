from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, flash
from models import db, Form, Submission, Permission, AuditLog
import json
import base64
import binascii
import re
from functools import wraps
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

forms_bp = Blueprint('forms', __name__)
HEADER_IMAGE_MAX_BYTES = 2 * 1024 * 1024
DATA_IMAGE_PATTERN = re.compile(
    r'^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=]+)$',
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
    return schema

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@forms_bp.route('/builder', methods=['GET', 'POST'])
@login_required
def builder():
    if request.method == 'POST':
        title = request.form.get('title')
        schema = request.form.get('schema')
        try:
            parsed_schema = json.loads(schema) if schema else {}
        except json.JSONDecodeError:
            flash("Invalid form data. Please try again.", "danger")
            return render_template('builder.html'), 400
        clean_schema = sanitize_schema(parsed_schema)
        
        new_form = Form(title=title, schema=json.dumps(clean_schema), created_by=session['username'])
        db.session.add(new_form)
        db.session.commit()
        
        # Add admin permission for the creator
        perm = Permission(form_id=new_form.id, username=session['username'], role='admin')
        
        # Log action
        log = AuditLog(action='CREATE_FORM', details=f"Form {new_form.id} created by {session['username']}")
        
        db.session.add(perm)
        db.session.add(log)
        db.session.commit()
        
        flash("Form created successfully!", "success")
        return redirect(url_for('forms.dashboard'))
        
    return render_template('builder.html')

@forms_bp.route('/form/<int:form_id>', methods=['GET', 'POST'])
@login_required
def view_form(form_id):
    form = Form.query.get_or_404(form_id)
    if not form.is_active:
        return render_template('form_closed.html', form=form)
        
    if request.method == 'POST':
        # form.io submits data as json
        data = request.json
        submission = Submission(
            form_id=form.id,
            data=json.dumps(data),
            submitted_by=session.get('username')
        )
        db.session.add(submission)
        db.session.commit()
        return jsonify({"status": "success", "message": "Submission received"}), 201
        
    permissions = Permission.query.filter_by(form_id=form.id).all()
    return render_template('form_view.html', form=form, permissions=permissions)

@forms_bp.route('/dashboard')
@login_required
def dashboard():
    # User's forms
    perms = Permission.query.filter_by(username=session['username']).all()
    form_ids = [p.form_id for p in perms]
    forms = Form.query.filter(Form.id.in_(form_ids)).all()
    
    # Create a mapping of form_id to role for easy template access
    roles = {p.form_id: p.role for p in perms}
    
    return render_template('dashboard.html', forms=forms, roles=roles)

@forms_bp.route('/form/<int:form_id>/close', methods=['POST'])
@login_required
def close_form(form_id):
    form = Form.query.get_or_404(form_id)
    
    perm = Permission.query.filter_by(form_id=form.id, username=session['username'], role='admin').first()
    if not perm:
        flash("You don't have permission to close this form.", "danger")
        return redirect(url_for('forms.dashboard'))
        
    form.is_active = False
    from datetime import datetime
    form.closed_at = datetime.utcnow()
    
    log = AuditLog(action='CLOSE_FORM', details=f"Form {form.id} closed by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    flash("Form closed. Data will be retained for 90 days.", "info")
    return redirect(url_for('forms.dashboard'))

@forms_bp.route('/form/<int:form_id>/reopen', methods=['POST'])
@login_required
def reopen_form(form_id):
    form = Form.query.get_or_404(form_id)
    perm = Permission.query.filter_by(form_id=form.id, username=session['username'], role='admin').first()
    if not perm:
        flash("You don't have permission to reopen this form.", "danger")
        return redirect(url_for('forms.dashboard'))
        
    form.is_active = True
    form.closed_at = None
    
    log = AuditLog(action='REOPEN_FORM', details=f"Form {form.id} reopened by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    flash("Form successfully reopened.", "success")
    return redirect(url_for('forms.dashboard'))

@forms_bp.route('/form/<int:form_id>/submissions')
@login_required
def view_submissions(form_id):
    form = Form.query.get_or_404(form_id)
    perm = Permission.query.filter_by(form_id=form.id, username=session['username']).first()
    if not perm:
        flash("You don't have permission to view these submissions.", "danger")
        return redirect(url_for('forms.dashboard'))
        
    submissions = Submission.query.filter_by(form_id=form.id).order_by(Submission.submitted_at.desc()).all()
    # Also fetch collaborators for management if admin
    collaborators = []
    is_admin = perm.role == 'admin'
    if is_admin:
        collaborators = Permission.query.filter_by(form_id=form.id).all()
        
    return render_template('submissions.html', form=form, submissions=submissions, is_admin=is_admin, collaborators=collaborators)

@forms_bp.route('/api/form/<int:form_id>/submission/<int:sub_id>', methods=['DELETE'])
@login_required
def delete_submission(form_id, sub_id):
    perm = Permission.query.filter_by(form_id=form_id, username=session['username']).first()
    if not perm:
        return jsonify({"error": "Unauthorized"}), 403
        
    sub = Submission.query.get_or_404(sub_id)
    if sub.form_id != form_id:
        return jsonify({"error": "Bad request"}), 400
        
    db.session.delete(sub)
    log = AuditLog(action='DELETE_SUBMISSION', details=f"Submission {sub_id} from Form {form_id} deleted by {session['username']}")
    db.session.add(log)
    db.session.commit()
    return jsonify({"status": "success"})

@forms_bp.route('/api/form/<int:form_id>/collaborator', methods=['POST'])
@login_required
def add_collaborator(form_id):
    perm = Permission.query.filter_by(form_id=form_id, username=session['username'], role='admin').first()
    if not perm:
        return jsonify({"error": "Only admins can add collaborators"}), 403
        
    username = request.json.get('username')
    if not username:
        return jsonify({"error": "Username required"}), 400
        
    existing = Permission.query.filter_by(form_id=form_id, username=username).first()
    if existing:
        return jsonify({"error": "User is already a collaborator"}), 400
        
    new_perm = Permission(form_id=form_id, username=username, role='viewer')
    db.session.add(new_perm)
    log = AuditLog(action='ADD_COLLABORATOR', details=f"Collaborator {username} added to Form {form_id} by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"status": "success", "username": username, "role": "viewer"})

@forms_bp.route('/api/form/<int:form_id>/collaborator/<username>', methods=['DELETE'])
@login_required
def remove_collaborator(form_id, username):
    perm = Permission.query.filter_by(form_id=form_id, username=session['username'], role='admin').first()
    if not perm:
        return jsonify({"error": "Only admins can remove collaborators"}), 403
        
    target_perm = Permission.query.filter_by(form_id=form_id, username=username).first()
    if not target_perm:
        return jsonify({"error": "Collaborator not found"}), 404
        
    if target_perm.role == 'admin' and target_perm.username == session['username']:
        return jsonify({"error": "Cannot remove yourself as admin"}), 400
        
    db.session.delete(target_perm)
    log = AuditLog(action='REMOVE_COLLABORATOR', details=f"Collaborator {username} removed from Form {form_id} by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"status": "success"})
