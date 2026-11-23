"""One-time cleanup of generated, unmodified synthetic agreements.

Only delete known generated records with is_demo=True and no user documents,
notes, invitations, or company card. Leave all operational records untouched.
"""
from sqlalchemy import select,delete
from app.database import SessionLocal
from app.models import Agreement,Attachment,AgreementNote,PartnerInvite,CompanyCard,Task,AuditEvent
with SessionLocal() as db:
    deleted=0
    for a in db.scalars(select(Agreement).where(Agreement.is_demo.is_(True))).all():
        if not (a.reference.startswith(("PILOT-","EXAMPLE-","DEMO-CIFRA-"))):
            continue
        linked=any(db.scalar(select(model.id).where(model.agreement_id==a.id).limit(1))
                   for model in (Attachment,AgreementNote,PartnerInvite,CompanyCard))
        if linked:continue
        db.execute(delete(Task).where(Task.agreement_id==a.id))
        db.execute(delete(AuditEvent).where(AuditEvent.entity_type=="agreement",
                                              AuditEvent.entity_id==a.id))
        db.delete(a)
        deleted+=1
    db.commit()
    print("Removed generated synthetic agreement records:",deleted)
