import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    
    # OAuth configuration
    WIKI_CLIENT_ID = os.environ.get("WIKI_CLIENT_ID", "ae071ef7591ed1771dbe77e5cdae49d5")
    WIKI_CLIENT_SECRET = os.environ.get("WIKI_CLIENT_SECRET", "08dcd9b3e19195e2611261ebf5d4e09e68112f65")
    OAUTH_MWURI = 'https://meta.wikimedia.org/w/index.py'
    
    # Database configuration
    if "TOOL_DATA_DIR" in os.environ:
        # We are on Toolforge
        TOOL_NAME = os.environ.get("TOOL_NAME", "dtoc")
        # For python toolforge library, it's easier to use a connection string or the toolforge module
        # For now, we will construct the connection string based on the standard MariaDB format
        db_user = os.environ.get("TOOLFORGE_DB_USER")
        db_password = os.environ.get("TOOLFORGE_DB_PASSWORD")
        db_host = "tools.db.svc.wikimedia.cloud"
        SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{db_user}:{db_password}@{db_host}/s55638__dtoc"
    else:
        # Local Development
        basedir = os.path.abspath(os.path.dirname(__file__))
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'dtoc.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
