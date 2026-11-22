"""Create a real manager from environment-provided credentials. Never print secrets."""
import os
from sqlalchemy import select
from app.database import Base, engine, SessionLocal
from app.models import User
from app.security import hash_password, validate_password
username=os.environ.get('INITIAL_ADMIN_USERNAME','').strip().lower()
password=os.environ.get('INITIAL_ADMIN_PASSWORD','')
fullname=os.environ.get('INITIAL_ADMIN_NAME','Руководитель системы').strip()
if not username or len(username)<4 or not validate_password(password):
    raise SystemExit('Provide INITIAL_ADMIN_USERNAME and a strong INITIAL_ADMIN_PASSWORD (>=12 with upper, lower, digit)')
Base.metadata.create_all(engine)
with SessionLocal() as db:
    if db.scalar(select(User.id).where(User.username==username)):
        raise SystemExit('Account exists; refusing to overwrite credentials')
    db.add(User(username=username,full_name=fullname,role='MANAGER',password_hash=hash_password(password)))
    db.commit()
print('Manager account created. Password is not printed.')