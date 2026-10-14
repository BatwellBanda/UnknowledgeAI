from database.connection import SessionLocal
from app.models.role import Role

db = SessionLocal()

roles = ["STUDENT", "LECTURER", "ADMIN"]

for role_name in roles:
    existing = db.query(Role).filter(Role.name == role_name).first()
    if not existing:
        db.add(Role(name=role_name))
        print(f"Added role: {role_name}")
    else:
        print(f"Role already exists: {role_name}")

db.commit()
db.close()