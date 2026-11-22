from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from .models import Agreement, AuditEvent, Attachment, User
from .security import require_role

STATUS_LABELS={
 "DRAFT":"Черновик", "REVISION_REQUIRED":"На доработке", "STAFF_REVIEW":"Проверка кафедры",
 "COORDINATOR_REVIEW":"Ответственный за практику", "OFFICE_REVIEW":"Отдел практик",
 "PARTNER_REVIEW":"Согласование предприятия", "COMPLETED":"Завершён",
}
STATUS_STEPS=["DRAFT","STAFF_REVIEW","COORDINATOR_REVIEW","OFFICE_REVIEW","PARTNER_REVIEW","COMPLETED"]
ACTIONS_LABELS={"submit":"Отправить на согласование","staff_approve":"Проверено кафедрой", "coordinator_approve":"Предварительно одобрено", "office_approve":"Подтверждено отделом практик","partner_approve":"Подтверждено предприятием", "return":"Вернуть на доработку"}

def log_event(db,actor,action,entity_type,entity_id,details=""):
    db.add(AuditEvent(actor_id=actor.id if actor else None,action=action,entity_type=entity_type,entity_id=entity_id,details=details[:1500]))

def possible_actions(user, agreement):
    st=agreement.status
    if user.role=="STUDENT" and agreement.student_id==user.id and st in {"DRAFT","REVISION_REQUIRED"}:
        return ["submit"]
    if user.role=="STAFF" and st=="STAFF_REVIEW": return ["staff_approve","return"]
    if user.role=="COORDINATOR" and st=="COORDINATOR_REVIEW":return ["coordinator_approve","return"]
    if user.role=="OFFICE" and st=="OFFICE_REVIEW":return ["office_approve","return"]
    if user.role=="ENTERPRISE" and agreement.enterprise_user_id==user.id and st=="PARTNER_REVIEW": return ["partner_approve","return"]
    return []

def advance_agreement(db,user,agreement,action):
    if action not in possible_actions(user,agreement):
        raise HTTPException(403, "Действие не разрешено на данном этапе")
    dt=datetime.utcnow()
    old=agreement.status
    if action=="submit":
        has_attachment=db.scalar(select(Attachment.id).where(Attachment.agreement_id==agreement.id).limit(1))
        if not has_attachment:
            raise HTTPException(422,"Сначала загрузите проект договора")
        agreement.staff_approved_at=None;agreement.coordinator_approved_at=None
        agreement.office_approved_at=None;agreement.partner_approved_at=None;agreement.completed_at=None
        agreement.submitted_at=dt
        agreement.status="STAFF_REVIEW"
    elif action=="staff_approve":
        agreement.staff_approved_at=dt;agreement.status="COORDINATOR_REVIEW"
    elif action=="coordinator_approve":
        agreement.coordinator_approved_at=dt
        agreement.status="OFFICE_REVIEW" if agreement.signing_order=="university_first" else "PARTNER_REVIEW"
    elif action=="office_approve":
        agreement.office_approved_at=dt
        agreement.status="COMPLETED" if agreement.partner_approved_at else "PARTNER_REVIEW"
    elif action=="partner_approve":
        agreement.partner_approved_at=dt
        agreement.status="COMPLETED" if agreement.office_approved_at else "OFFICE_REVIEW"
    elif action=="return":
        agreement.status="REVISION_REQUIRED"
    if agreement.status=="COMPLETED":agreement.completed_at=dt
    agreement.updated_at=dt
    log_event(db,user,"status_changed","agreement",agreement.id,f"{STATUS_LABELS.get(old,old)} → {STATUS_LABELS.get(agreement.status,agreement.status)} · {ACTIONS_LABELS.get(action,action)}. Это электронное согласование в прототипе, не УКЭП.")
    return agreement