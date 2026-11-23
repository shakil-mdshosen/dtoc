#!/bin/bash
# setup_jobs.sh - Run this on Toolforge to configure the daily Janitor cronjob
# NOTE: jobs must run with the tool venv Python (~/www/python/venv/bin/python),
# not the system python3.11: the job container has no Flask / Google libraries
# installed, so sync_worker.py and janitor.py fail on import with system Python.

# Delete the job if it already exists to update it
toolforge-jobs delete janitor-purge-job || true

# Create the new scheduled job (runs daily at midnight)
toolforge-jobs run janitor-purge-job \
    --command "~/www/python/venv/bin/python ~/www/python/src/janitor.py" \
    --image python3.11 \
    --schedule "@daily"

echo "Janitor job scheduled successfully. Use 'toolforge-jobs list' to verify."

# Google Sheets sync worker (runs every 5 minutes). Only does work when
# GOOGLE_SYNC_ENABLED=1 and the Google credentials are configured; otherwise
# it exits immediately. Delete it with: toolforge-jobs delete sheets-sync-job
toolforge-jobs delete sheets-sync-job || true

toolforge-jobs run sheets-sync-job \
    --command "~/www/python/venv/bin/python ~/www/python/src/sync_worker.py" \
    --image python3.11 \
    --schedule "*/5 * * * *"

echo "Sheets sync job scheduled successfully. Use 'toolforge-jobs list' to verify."
