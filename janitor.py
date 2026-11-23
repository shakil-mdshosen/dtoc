import os
from datetime import datetime, timedelta
from app import create_app
from models import db, Form, Submission, AuditLog, SyncQueue

def run_janitor(app=None):
    if app is None:
        app = create_app()
    with app.app_context():
        # Find all closed forms older than 90 days
        cutoff_date = datetime.utcnow() - timedelta(days=90)

        expired_forms = Form.query.filter(
            Form.is_active == False,
            Form.closed_at <= cutoff_date
        ).all()

        deleted_count = 0
        skipped_count = 0
        for form in expired_forms:
            # Google Sheets sync (design §9, Copilot review Q7):
            # if this form still has unsent queue rows (pending/sending),
            # SKIP the purge for this form this run. Purging would either
            # fail on the queue rows' foreign keys or silently lose
            # responses that were never copied to the sheet. The form
            # will be retried on the next janitor run; sent/failed rows
            # do not block the purge. This can delay a purge past 90
            # days for unsent rows only, which the privacy notice says.
            unsent = SyncQueue.query.filter(
                SyncQueue.form_id == form.id,
                SyncQueue.status.in_(('pending', 'sending'))).count()
            if unsent:
                skipped_count += 1
                db.session.add(AuditLog(
                    action='DATA_PURGE_SKIPPED',
                    details=f"Skipped purge for Form {form.id}: "
                            f"{unsent} unsent Google Sheets queue row(s)."))
                continue
            # Delete all submissions for this form. Delete their queue
            # rows first so no foreign-key error is left behind.
            submissions = Submission.query.filter_by(form_id=form.id).all()
            SyncQueue.query.filter_by(form_id=form.id).delete()
            for sub in submissions:
                db.session.delete(sub)
                deleted_count += 1

            # Log the deletion
            log = AuditLog(
                action='DATA_PURGE',
                details=f"Purged {len(submissions)} submissions for Form {form.id} due to 90-day retention policy."
            )
            db.session.add(log)

            # We keep the Form record and the AuditLogs, just delete the raw Submission data
            # Or we could delete the form too, but usually audit requires keeping the metadata

        db.session.commit()
        print(f"Janitor run complete. Purged {deleted_count} submissions across {len(expired_forms) - skipped_count} forms; skipped {skipped_count} form(s) with unsent Sheets queue rows.")

if __name__ == '__main__':
    run_janitor()
