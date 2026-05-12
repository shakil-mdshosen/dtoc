# DTOC: Data Retention & Form Collection Tool

The **DTOC (Data Retention & Form Collection)** tool is a modernized, mobile-responsive application hosted on Wikimedia Toolforge. It provides Wikimedia communities and administrators with a secure, highly customizable, and privacy-focused alternative to third-party services like Google Forms.

## Key Features

### 1. Secure Authentication
- **Wikimedia OAuth Integration:** All access to the application—from creating forms to simply viewing and submitting responses—is protected by Wikimedia OAuth. Anonymous submissions are disabled.
- **Identity Logging:** The identity of the submitter is always logged securely with their response, ensuring accountability and preventing spam.

### 2. Advanced Form Builder
- **Rich Media & Formatting:** Forms support rich-text descriptions (bold, italics, links) and custom Base64 Header Images to personalize the user experience.
- **Multiple Input Types:** Choose from Short Answer, Paragraph, Multiple Choice, Checkboxes, Dropdowns, and Date Pickers.
- **Material Design UI:** The builder and viewer utilize a beautiful, mobile-first Google Forms-style aesthetic built with Tailwind CSS.

### 3. Collaboration & Access Control
- **Granular Permissions:** Form creators can add other Wikimedia users as "Collaborators", allowing them to view and manage submissions.
- **Superuser Owner Dashboard:** The system supports a global `OWNER_USERNAME`. The designated owner has access to a centralized dashboard (`/owner/dashboard`) to monitor all forms across the entire Toolforge instance, view response counts, and filter by creator or status.

### 4. Comprehensive Data Export
Form administrators can export their collected data in three highly structured formats:
- **CSV:** Standard comma-separated values.
- **JSON:** Machine-readable data structure, perfect for bots or API integrations.
- **Direct Excel (`.xlsx`):** A stylized, native Microsoft Excel file with perfectly formatted rows, columns, and list parsing.

---

## Technical Stack

- **Backend:** Python 3, Flask
- **Database:** MariaDB (via Toolforge `replica.my.cnf`), SQLAlchemy ORM
- **Frontend:** HTML5, Vanilla JavaScript, Tailwind CSS (CDN)
- **Authentication:** `mwoauth`, `requests-oauthlib`

---

## Required Environment Variables

For the application to function correctly on Toolforge, the following environment variables must be configured via the `toolforge envvars` command:

| Variable Name | Description |
| :--- | :--- |
| `WIKI_CLIENT_ID` | The OAuth Consumer Key from Meta-Wiki. |
| `WIKI_CLIENT_SECRET` | The OAuth Consumer Secret from Meta-Wiki. |
| `OWNER_USERNAME` | (Optional) The exact Wikimedia username of the superuser who has access to the `/owner/dashboard`. |

---

## Essential Toolforge Commands

As a maintainer of the DTOC tool, you will frequently use the Toolforge bastion terminal. Below are the basic commands required to access and manage the tool.

> [!IMPORTANT]
> **Always switch to the tool account before running commands!**
> When you log into Toolforge via SSH, you are on your personal user account. You **must** become the tool user to access the code and restart the server.

### Accessing the Tool Account
```bash
# Switch from your personal account to the dtoc tool account
become dtoc
```

### Updating the Code & Restarting the Server
```bash
# Navigate to the source code directory
cd ~/www/python/src/

# Pull the latest changes from GitHub
git pull

# Restart the uWSGI webservice to apply the changes
webservice restart
```

### Managing Python Dependencies
If new libraries are added to `requirements.txt`, you must install them into the virtual environment:
```bash
cd ~/www/python/src/

# Activate the virtual environment
source ../venv/bin/activate

# Install the updated requirements
pip install -r requirements.txt

# Restart the webservice
webservice restart
```

### Managing Environment Variables
```bash
# List all active environment variables
toolforge envvars list

# Create or Update a variable
toolforge envvars create OWNER_USERNAME "YourWikimediaUsername"

# Delete a variable
toolforge envvars delete OLD_VARIABLE_NAME
```

### Viewing Application Error Logs
If the website displays an "Internal Server Error", use this command to view the Python crash traceback:
```bash
# Filter out standard web requests to easily see the python errors
grep -a -v "GET /" /data/project/dtoc/uwsgi.log | tail -n 50
```
