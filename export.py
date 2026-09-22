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
    headers = column_headers(load_schema(form), datas)
    return form, (submissions, datas, headers)


def _denied():
    flash("You don't have permission to export this form.", "danger")
    return redirect(url_for('forms.dashboard'))


@export_bp.route('/form/<int:form_id>/export/csv')
@login_required
def export_csv(form_id):
    form, loaded = _load_export(form_id, 'EXPORT_CSV', '')
    if loaded is None:
        return _denied()
    submissions, datas, headers = loaded

    si = StringIO()
    cw = csv.writer(si)
    if not submissions:
        cw.writerow(["No submissions found."])
    else:
        cw.writerow(['ID', 'Submitted At', 'Submitted By'] + headers)
        for sub, data in zip(submissions, datas):
            row = [sub.id, sub.submitted_at.isoformat(), sub.submitted_by]
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
    submissions, datas, _ = loaded

    output_data = [
        {
            "id": sub.id,
            "submitted_at": sub.submitted_at.isoformat(),
            "submitted_by": sub.submitted_by,
            "data": data
        }
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
    submissions, datas, field_headers = loaded

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Submissions"

    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")

    if not submissions:
        ws.append(["No submissions found."])
    else:
        all_headers = ['ID', 'Submitted At', 'Submitted By'] + field_headers
        for col_idx, header in enumerate(all_headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        # Freeze the header row
        ws.freeze_panes = "A2"

        for sub, data in zip(submissions, datas):
            row = [sub.id, sub.submitted_at.isoformat(), sub.submitted_by]
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
