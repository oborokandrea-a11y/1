"""Create one non-personal pilot account per role from hosting-only secrets.
No passwords are stored in GitHub. Existing users are never overwritten.
"""
import os
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
from app.security import hash_password, validate_password

ACCOUNTS = {
    "STUDENT": ("student-demo", "Студент (учебная роль)"),
    "STAFF": ("staff-demo", "Сотрудник кафедры (учебная роль)"),
    "COORDINATOR": ("coordinator-demo", "Ответственный за практику (учебная роль)"),
    "OFFICE": ("office-demo", "Отдел практик (учебная роль)"),
    "ENTERPRISE": ("enterprise-demo", "Представитель предприятия (учебная роль)"),
}
with SessionLocal() as db:
    for role, (username, label) in ACCOUNTS.items():
        password = os.getenv("PILOT_PASSWORD_" + role, "")
        if not validate_password(password):
            continue
        if db.scalar(select(User.id).where(User.username == username)):
            continue
        db.add(User(username=username, full_name=label, role=role,
                    password_hash=hash_password(password),
                    organization_name="Учебная организация" if role == "ENTERPRISE" else ""))
        db.commit()
        print("Created pilot account:", role)
