"""Create a LECTURER or ADMIN account, or change an existing account's role or password.

Run from Backend:
    python -m scripts.create_user --email lecturer@uni.ac --name "Lecturer Name" --role LECTURER
    python -m scripts.create_user --email student@uni.ac --role STUDENT --reset-password
The password is read interactively and never appears in shell history.
"""
import argparse
import getpass
import sys
from app.main import app  # noqa: F401  (registers every model before relationships are configured)
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User
from database.connection import SessionLocal


def read_password() -> str:
    password = getpass.getpass("Password (12-72 bytes): ")
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        sys.exit("Password must be 12 characters to 72 UTF-8 bytes.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return password


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", help="Full name (required for a new account)")
    parser.add_argument("--role", required=True, type=str.upper, choices=["STUDENT", "LECTURER", "ADMIN"])
    parser.add_argument("--reset-password", action="store_true", help="Set a new password for an existing account")
    args = parser.parse_args()

    with SessionLocal() as db:
        role = db.query(Role).filter(Role.name == args.role).first()
        if role is None:
            sys.exit("Roles are missing; run seed_roles.py first.")
        user = db.query(User).filter(User.email == args.email).first()
        if user is not None:
            user.role_id = role.id
            if args.reset_password:
                user.hashed_password = hash_password(read_password())
            # Revoke tokens issued under the previous role or password.
            user.token_version = (user.token_version or 0) + 1
            db.commit()
            print(f"Updated {args.email} ({args.role}){' with a new password' if args.reset_password else ''}.")
            return
        if not args.name:
            sys.exit("--name is required when creating a new account.")
        db.add(User(full_name=args.name.strip(), email=args.email, hashed_password=hash_password(read_password()), role_id=role.id))
        db.commit()
        print(f"Created {args.role} account {args.email}.")


if __name__ == "__main__":
    main()
