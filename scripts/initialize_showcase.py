"""Idempotent, explicitly simulated 103-case showcase from anonymized baseline.

These are NOT real signed contracts. Never overwrite operational agreements.
"""
import csv
from datetime import datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Agreement, User, AuditEvent
from app.settings import ROOT

STAGES=("COMPLETED","STAFF_REVIEW","COORDINATOR_REVIEW","OFFICE_REVIEW","PARTNER_REVIEW","REVISION_REQUIRED","DRAFT")
with SessionLocal() as db:
    owner=db.scalar(select(User).where(User.username=="student-demo"))
    if owner is None:
        owner=db.scalar(select(User).where(User.role=="MANAGER"))
    if owner is None:
        print("Pilot showcase postponed: no existing owner account")
    else:
        path=ROOT/"data"/"baseline_103_anonymized.csv"
        if path.exists():
            created=0
            with path.open(encoding="utf-8-sig",newline="") as source:
                for i,row in enumerate(csv.DictReader(source)):
                    ref="EXAMPLE-"+row["source_reference"]
                    legacy=db.scalar(select(Agreement).where(Agreement.reference=="PILOT-"+row["source_reference"]))
                    if legacy:
                        legacy.reference=ref
                        legacy.title="[ОБРАЗЕЦ] Обезличенная запись "+row["source_reference"]
                        continue
                    if db.scalar(select(Agreement.id).where(Agreement.reference==ref)):
                        continue
                    # 57 completed / 46 active is a DEMONSTRATION distribution,
                    # not an inference about real signed contracts.
                    if i<57:status="COMPLETED"
                    elif i<69:status="STAFF_REVIEW"
                    elif i<79:status="COORDINATOR_REVIEW"
                    elif i<87:status="OFFICE_REVIEW"
                    elif i<95:status="PARTNER_REVIEW"
                    elif i<100:status="REVISION_REQUIRED"
                    else:status="DRAFT"
                    case=Agreement(
                        reference=ref,
                        title="[ОБРАЗЕЦ] Обезличенная запись "+row["source_reference"],
                        practice_type=row["practice_type"],
                        student_id=owner.id,
                        company_name="Обезличенное предприятие (пилот)",
                        practice_start=row["submitted_date"],
                        practice_end=row["signed_date"],
                        status=status,
                        is_demo=True,
                        signing_order="university_first",
                        description=("Демонстрационная запись на основе обезличенного набора. "
                                     "Статус смоделирован для проверки интерфейса; "
                                     "не подтверждает подписание или завершение реального договора. "
                                     "Исходный идентификатор: "+row["source_reference"]),
                    )
                    if status=="COMPLETED":
                        case.completed_at=datetime.fromisoformat(row["signed_date"])
                    db.add(case)
                    db.flush()
                    db.add(AuditEvent(actor_id=None,action="pilot_case_imported",
                        entity_type="agreement",entity_id=case.id,
                        details="Пилотная модель; статус смоделирован, не является подтверждением подписания"))
                    created+=1
            db.commit()
            print("Pilot showcase records added:",created)
