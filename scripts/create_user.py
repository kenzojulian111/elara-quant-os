import getpass
from flask import Flask

from config.settings import Config
from database.models import db, User


def main():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = Config.SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        email = input("Email: ").strip()
        if not email:
            print("Email is required.")
            return
        if User.query.filter_by(email=email).first():
            print("User already exists.")
            return
        password = getpass.getpass("Password: ")
        if not password:
            print("Password is required.")
            return
        name = input("Name (optional): ").strip() or "Admin"
        role = input("Role (default=superuser): ").strip() or "superuser"

        user = User(email=email, name=name, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"User created: {email}")


if __name__ == "__main__":
    main()

