from flask import Blueprint, session, redirect, url_for, flash, Response
from models import db, Form, Submission, Permission, AuditLog
import json
import csv
from io import StringIO, BytesIO
from forms_api import login_required, load_schema, load_data
from form_schema import column_headers, format_value
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

export_bp = Blueprint('export', __name__)


def _load_export(form_id, action, label):
    """Shared permission check, audit logging and data loading for exports."""
    form = Form.query.get_or_404(form_id)
    perm = Permission.query.filter_by(form_id=form.id, username=session['username']).first()
    if not perm:
        return form, None
    submissions = Submission.query.filter_by(form_id=form.id).order_by(Submission.submitted_at).all()
    db.session.add(AuditLog(action=action, details=f"Form {form.id} exported{label} by {session['username']}"))
    db.session.commit()
    datas = [load_data(sub) for sub in submissions]
    schema = load_schema(form)
    headers = column_headers(schema, datas)
    return form, (submissions, datas, headers, includes_email(schema, submissions))


def includes_email(schema, submissions):
    """Show the email column if the form collects emails or older responses have one."""
    return bool(schema['settings'].get('collect_email') or any(s.submitted_email for s in submissions))


def _meta_headers(with_email):
    return ['ID', 'Submitted At', 'Submitted By'] + (['Submitted Email'] if with_email else [])


def _meta_row(sub, with_email):
    row = [sub.id, sub.submitted_at.isoformat(), sub.submitted_by]
    if with_email:
        row.append(sub.submitted_email or '')
    return row


def _denied():
    flash("You don't have permission to export this form.", "danger")
    return redirect(url_for('forms.dashboard'))


@export_bp.route('/form/<int:form_id>/export/csv')
@login_required
def export_csv(form_id):
    form, loaded = _load_export(form_id, 'EXPORT_CSV', '')
    if loaded is None:
        return _denied()
    submissions, datas, headers, with_email = loaded

    si = StringIO()
    cw = csv.writer(si)
    if not submissions:
        cw.writerow(["No submissions found."])
    else:
        cw.writerow(_meta_headers(with_email) + headers)
        for sub, data in zip(submissions, datas):
            row = _meta_row(sub, with_email)
            row.extend(format_value(data.get(h, '')) for h in headers)
            cw.writerow(row)

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=form_{form.id}_export.csv"}
    )


@export_bp.route('/form/<int:form_id>/export/json')
@login_required
def export_json(form_id):
    form, loaded = _load_export(form_id, 'EXPORT_JSON', ' as JSON')
    if loaded is None:
        return _denied()
    submissions, datas, _, with_email = loaded

    output_data = [
        dict(
            {"id": sub.id, "submitted_at": sub.submitted_at.isoformat(), "submitted_by": sub.submitted_by},
            **({"submitted_email": sub.submitted_email} if with_email else {}),
            data=data,
        )
        for sub, data in zip(submissions, datas)
    ]

    return Response(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-disposition": f"attachment; filename=form_{form.id}_export.json"}
    )


@export_bp.route('/form/<int:form_id>/export/excel')
@login_required
def export_excel(form_id):
    form, loaded = _load_export(form_id, 'EXPORT_EXCEL', ' as Excel')
    if loaded is None:
        return _denied()
    submissions, datas, field_headers, with_email = loaded

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Submissions"

    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")

    if not submissions:
        ws.append(["No submissions found."])
    else:
        all_headers = _meta_headers(with_email) + field_headers
        for col_idx, header in enumerate(all_headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        # Freeze the header row
        ws.freeze_panes = "A2"

        for sub, data in zip(submissions, datas):
            row = _meta_row(sub, with_email)
            row.extend(format_value(data.get(h, '')) for h in field_headers)
            ws.append(row)

        # Auto-fit column widths (materialise the column into a list to avoid double-iteration)
        for col in ws.columns:
            cells = list(col)
            max_len = max((len(str(cell.value)) if cell.value else 0) for cell in cells)
            ws.column_dimensions[cells[0].column_letter].width = min(max_len + 4, 60)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return Response(
        output.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-disposition": f"attachment; filename=form_{form.id}_export.xlsx"}
    )
