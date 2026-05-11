from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, flash
from models import db, Form, Submission, Permission, AuditLog
import json
from functools import wraps

forms_bp = Blueprint('forms', __name__)

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
        
        new_form = Form(title=title, schema=schema, created_by=session['username'])
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
        
    return render_template('form_view.html', form=form)

@forms_bp.route('/dashboard')
@login_required
def dashboard():
    # User's forms
    perms = Permission.query.filter_by(username=session['username']).all()
    form_ids = [p.form_id for p in perms]
    forms = Form.query.filter(Form.id.in_(form_ids)).all()
    
    return render_template('dashboard.html', forms=forms)

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
