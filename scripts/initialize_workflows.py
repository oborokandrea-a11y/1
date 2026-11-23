"""Idempotent, clearly labelled demonstration workflows for every pilot role."""
from datetime import datetime, timedelta
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User, Agreement, Task, AuditEvent

STAGES = [
    ("DRAFT", "Подготовить проект договора и загрузить файл", "STUDENT"),
    ("STAFF_REVIEW", "Проверить реквизиты и содержание договора", "STAFF"),
    ("COORDINATOR_REVIEW", "Проверить сроки практики и согласовать маршрут", "COORDINATOR"),
    ("OFFICE_REVIEW", "Проверить комплект документов для оформления", "OFFICE"),
    ("PARTNER_REVIEW", "Проверить договор со стороны предприятия", "ENTERPRISE"),
]
with SessionLocal() as db:
    users = {u.role:u for u in db.scalars(select(User).where(User.username.in_([
        "student-demo","staff-demo","coordinator-demo","office-demo","enterprise-demo"
    ]))).all()}
    if not all(k in users for k in ("STUDENT","STAFF","COORDINATOR","OFFICE","ENTERPRISE")):
        print("Pilot workflows postponed: not all demo roles exist.")
    else:
        for i,(status,task_title,role) in enumerate(STAGES,1):
            reference=f"DEMO-CIFRA-{i:03d}"
            case=db.scalar(select(Agreement).where(Agreement.reference==reference))
            if case is None:
                case=Agreement(reference=reference,
                    title=f"[УЧЕБНЫЙ ПРИМЕР] Договор о производственной практике — этап {i}",
                    practice_type="Производственная",student_id=users["STUDENT"].id,
                    company_name="Учебное предприятие (вымышленная организация)",
                    enterprise_user_id=users["ENTERPRISE"].id,
                    practice_start="2026-12-01",practice_end="2026-12-28",
                    signing_order="university_first",status=status,is_demo=True,
                    description="Демонстрационный кейс. Данные и организация вымышленные. Не является настоящим договором.",
                    deadline_at=datetime.utcnow()+timedelta(days=i+2))
                db.add(case);db.flush()
                db.add(AuditEvent(actor_id=None,action="demo_case_created",entity_type="agreement",
                    entity_id=case.id,details=f"Учебный кейс для этапа {status}"))
            task=db.scalar(select(Task).where(Task.agreement_id==case.id,Task.title==task_title))
            if task is None:
                db.add(Task(title=task_title,
                    details=f"Учебная задача по {reference}. Откройте договор и выполните действие, разрешённое вашей роли. Документ учебный.",
                    created_by_id=users["COORDINATOR"].id,assigned_to_id=users[role].id,
                    agreement_id=case.id,due_at=datetime.utcnow()+timedelta(days=i+2),
                    priority="normal" if i!=4 else "high",status="TODO"))
        db.commit()
        print("Demo workflows ensured: five cases and five role-specific tasks.")
