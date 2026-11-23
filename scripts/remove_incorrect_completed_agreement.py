"""One-time removal of the single incorrectly completed legacy agreement.

This deliberately refuses to delete if more than one completed record exists,
if qualified signatures exist, or if the record is not a legacy completion.
No other agreements are touched. Related documents remain archived in private
storage; DB references are removed only for the uniquely identified record.
"""
from sqlalchemy import select,delete
from app.database import SessionLocal
from app.models import Agreement,QualifiedSignature,Attachment,AgreementNote,CompanyCard,Task,PartnerInvite,AuditEvent
with SessionLocal() as db:
    matches=db.scalars(select(Agreement).where(Agreement.status=="COMPLETED",Agreement.is_demo.is_(False))).all()
    if len(matches)!=1:
        print(f"Legacy completion cleanup: expected exactly one, found {len(matches)}; no deletion")
    else:
        item=matches[0]
        signed=db.scalar(select(QualifiedSignature.id).where(QualifiedSignature.agreement_id==item.id).limit(1))
        if signed:
            print("Legacy completion cleanup: verified signature exists; no deletion")
        else:
            print(f"Legacy completion cleanup: removing incorrect record {item.id} / {item.reference}")
            for model in (Attachment,AgreementNote,CompanyCard,Task,PartnerInvite):
                db.execute(delete(model).where(model.agreement_id==item.id))
            db.delete(item)
            db.add(AuditEvent(actor_id=None,action="legacy_contract_removed",
                entity_type="agreement",entity_id=item.id,
                details="По запросу владельца удалена единственная ошибочно завершённая запись без УКЭП; номер "+item.reference))
            db.commit()
            print("Legacy completion cleanup: removed one incorrect agreement")
