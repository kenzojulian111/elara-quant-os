"""
Bootstrap database for Elara.
Creates all tables and optional admin bootstrap user from environment:
- ADMIN_BOOTSTRAP_EMAIL
- ADMIN_BOOTSTRAP_PASSWORD
- ADMIN_BOOTSTRAP_NAME (default: Admin)
- ADMIN_BOOTSTRAP_ROLE (default: superuser)
"""

import os
import sys
from flask import Flask

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config.settings import Config
from database.models import db
from database.database_manager import DatabaseManager


def main():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        db_manager = DatabaseManager(app)
        created = db_manager.ensure_bootstrap_user()
        msg = "Bootstrap user created." if created else "Bootstrap user skipped/existing."
        print(f"[init_db] DB ready at {Config.SQLALCHEMY_DATABASE_URI}. {msg}")


if __name__ == "__main__":
    main()

