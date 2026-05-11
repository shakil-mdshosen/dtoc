import os
from datetime import datetime, timedelta
from app import create_app
from models import db, Form, Submission, AuditLog

def run_janitor():
    app = create_app()
    with app.app_context():
        # Find all closed forms older than 90 days
        cutoff_date = datetime.utcnow() - timedelta(days=90)
        
        expired_forms = Form.query.filter(
            Form.is_active == False,
            Form.closed_at <= cutoff_date
        ).all()
        
        deleted_count = 0
        for form in expired_forms:
            # Delete all submissions for this form
            submissions = Submission.query.filter_by(form_id=form.id).all()
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
        print(f"Janitor run complete. Purged {deleted_count} submissions across {len(expired_forms)} forms.")

if __name__ == '__main__':
    run_janitor()
