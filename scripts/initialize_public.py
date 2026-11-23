"""Safe idempotent first-start initializer for the connected production database.

Never prints passwords and never replaces an existing manager account.
"""
import csv
import os
from sqlalchemy import select
from app.database import Base, engine, SessionLocal
from app.models import User, BaselineObservation, AgreementNumberSequence
from app.security import hash_password, validate_password
from app.settings import ROOT, settings

Base.metadata.create_all(engine)
# Add newly introduced optional company-card columns to existing SQLite databases.
# create_all() creates new tables but does not migrate existing ones.
from sqlalchemy import inspect, text
with engine.begin() as conn:
    existing = {c["name"] for c in inspect(conn).get_columns("company_cards")} if inspect(conn).has_table("company_cards") else set()
    for column, size in {"company_short_name":250,"residency":30,"registration_country":120,
                         "company_actual_address":450,"company_phone":60,"company_fax":60,
                         "bank_account":40,"bank_name":250,"correspondent_account":40,"bank_bik":20}.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE company_cards ADD COLUMN {column} VARCHAR({size}) DEFAULT '' NOT NULL"))

with SessionLocal() as db:
    # Highest documented 2026 number in the supplied practice register: 1191/26.
    # Never reset an existing sequence or overwrite issued numbers.
    if not db.get(AgreementNumberSequence, 2026):
        db.add(AgreementNumberSequence(year=2026, last_number=1191))
        db.commit()
    if not db.scalar(select(User.id).where(User.role == "MANAGER")):
        username = os.getenv("INITIAL_ADMIN_USERNAME", "").strip().lower()
        password = os.getenv("INITIAL_ADMIN_PASSWORD", "")
        full_name = os.getenv("INITIAL_ADMIN_NAME", "Руководитель проекта Цифра").strip()
        if len(username) < 4 or not validate_password(password):
            raise SystemExit("First launch needs INITIAL_ADMIN_USERNAME and strong INITIAL_ADMIN_PASSWORD in hosting secrets.")
        if db.scalar(select(User.id).where(User.username == username)):
            raise SystemExit("INITIAL_ADMIN_USERNAME is already taken; choose another.")
        db.add(User(username=username, full_name=full_name, role="MANAGER", password_hash=hash_password(password)))
        db.commit()
        print("First manager account initialized; password not printed.")

    # Original source is a de-identified pilot baseline. Do not import real identifiable contracts.
    path = ROOT / "data" / "baseline_103_anonymized.csv"
    if path.exists():
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                if db.scalar(select(BaselineObservation.id).where(
                    BaselineObservation.source_reference == item["source_reference"]
                )):
                    continue
                db.add(BaselineObservation(
                    source_reference=item["source_reference"], practice_type=item["practice_type"],
                    submitted_date=item["submitted_date"], forwarded_date=item["forwarded_date"],
                    signed_date=item["signed_date"], campus_days=int(item["campus_days"]),
                    waiting_days=int(item["waiting_days"]), visits=int(item["visits"]),
                    returns=int(item["returns"]), lost=item["lost"] == "1",
                    evidence=item["evidence"], external_delay_days=int(item["external_delay_days"])
                ))
                count += 1
        db.commit()
        print(f"Anonymized pilot baseline imported: {count} new observations.")