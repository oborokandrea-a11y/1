"""Normalize existing account display names without changing access or credentials."""
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
with SessionLocal() as db:
    changed=0
    for user in db.scalars(select(User)).all():
        old=user.full_name or ""
        new=old.replace(" (учебная роль)","").replace("(учебная роль)","").strip()
        if new!=old:
            user.full_name=new
            changed+=1
        if user.organization_name=="Учебная организация":
            user.organization_name="Организация"
            changed+=1
    db.commit()
    print(f"Account display names normalized: {changed}")
