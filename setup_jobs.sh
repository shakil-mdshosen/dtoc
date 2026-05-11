#!/bin/bash
# setup_jobs.sh - Run this on Toolforge to configure the daily Janitor cronjob

# Delete the job if it already exists to update it
toolforge-jobs delete janitor-purge-job || true

# Create the new scheduled job (runs daily at midnight)
toolforge-jobs run janitor-purge-job \
    --command "python3.11 ~/www/python/src/janitor.py" \
    --image python3.11 \
    --schedule "@daily"

echo "Janitor job scheduled successfully. Use 'toolforge-jobs list' to verify."
