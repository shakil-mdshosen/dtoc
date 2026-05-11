from flask import Blueprint, session, redirect, url_for, flash, Response
from models import db, Form, Submission, Permission, AuditLog
import json
import csv
from io import StringIO
from forms_api import login_required

export_bp = Blueprint('export', __name__)

@export_bp.route('/form/<int:form_id>/export/csv')
@login_required
def export_csv(form_id):
    form = Form.query.get_or_404(form_id)
    
    perm = Permission.query.filter_by(form_id=form.id, username=session['username']).first()
    if not perm:
        flash("You don't have permission to export this form.", "danger")
        return redirect(url_for('forms.dashboard'))
        
    submissions = Submission.query.filter_by(form_id=form.id).all()
    
    # Log export
    log = AuditLog(action='EXPORT_CSV', details=f"Form {form.id} exported by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    # Generate CSV using standard library since pandas isn't available locally
    si = StringIO()
    cw = csv.writer(si)
    
    if not submissions:
        cw.writerow(["No submissions found."])
    else:
        # Extract headers from the first submission
        first_data = json.loads(submissions[0].data)
        if 'data' in first_data:
            # Form.io nests data under 'data' key usually
            first_data = first_data['data']
        headers = list(first_data.keys())
        cw.writerow(['ID', 'Submitted At', 'Submitted By'] + headers)
        
        for sub in submissions:
            data = json.loads(sub.data)
            if 'data' in data:
                data = data['data']
            row = [sub.id, sub.submitted_at.isoformat(), sub.submitted_by]
            row.extend([data.get(h, '') for h in headers])
            cw.writerow(row)
            
    output = si.getvalue()
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=form_{form.id}_export.csv"}
    )

@export_bp.route('/form/<int:form_id>/export/json')
@login_required
def export_json(form_id):
    form = Form.query.get_or_404(form_id)
    
    perm = Permission.query.filter_by(form_id=form.id, username=session['username']).first()
    if not perm:
        flash("You don't have permission to export this form.", "danger")
        return redirect(url_for('forms.dashboard'))
        
    submissions = Submission.query.filter_by(form_id=form.id).all()
    
    # Log export
    log = AuditLog(action='EXPORT_JSON', details=f"Form {form.id} exported as JSON by {session['username']}")
    db.session.add(log)
    db.session.commit()
    
    output_data = []
    for sub in submissions:
        data = json.loads(sub.data)
        if 'data' in data:
            data = data['data']
        output_data.append({
            "id": sub.id,
            "submitted_at": sub.submitted_at.isoformat(),
            "submitted_by": sub.submitted_by,
            "data": data
        })
        
    return Response(
        json.dumps(output_data, indent=2),
        mimetype="application/json",
        headers={"Content-disposition": f"attachment; filename=form_{form.id}_export.json"}
    )
