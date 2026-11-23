"""Browser signing routes. CAdES detached signature is checked by a trusted verifier."""
import base64
import hashlib
import json
from datetime import datetime
from fastapi import APIRouter,Request,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import get_db
from .models import Agreement,Attachment,QualifiedSignature
from .security import require_user,can_view_agreement,require_csrf
from .storage import get_file,save_file
from .ukep import verify_detached_cades
from .services import log_event
router=APIRouter()

def signing_context(db,user,agreement_id):
    agreement=db.get(Agreement,agreement_id)
    if not agreement or agreement.is_demo or not can_view_agreement(user,agreement):
        raise HTTPException(404,"Договор не найден")
    if user.role=="OFFICE":side="UNIVERSITY"
    elif user.role=="ENTERPRISE" and agreement.enterprise_user_id==user.id:side="ENTERPRISE"
    else:raise HTTPException(403,"УКЭП доступна только уполномоченным подписантам сторон")
    if not agreement.office_approved_at or not agreement.partner_approved_at:
        raise HTTPException(409,"Не завершено внутреннее согласование обеих сторон")
    if agreement.status not in ("SIGNING_PENDING","COMPLETED"):
        raise HTTPException(409,"Договор не находится на этапе подписания")
    return agreement,side

def target_document(db,agreement_id):
    # Signing is only possible for an immutable uploaded PDF, not an on-the-fly
    # regenerated preview whose bytes might change.
    doc=db.scalar(select(Attachment).where(
        Attachment.agreement_id==agreement_id,Attachment.filename.ilike("%.pdf")
    ).order_by(Attachment.version_no.desc(),Attachment.id.desc()).limit(1))
    if not doc:raise HTTPException(409,"Для УКЭП загрузите окончательную PDF-версию договора")
    return doc

@router.get("/agreements/{agreement_id}/ukep/document")
def ukep_document(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    agreement,side=signing_context(db,user,agreement_id)
    doc=target_document(db,agreement_id)
    if db.scalar(select(QualifiedSignature.id).where(QualifiedSignature.agreement_id==agreement.id).limit(1)):
        # All signers must use exactly the same frozen PDF.
        signed=db.scalar(select(QualifiedSignature).where(QualifiedSignature.agreement_id==agreement.id).limit(1))
        if signed.attachment_id!=doc.id:raise HTTPException(409,"Версия договора изменилась после подписания")
    return Response(get_file(doc.object_key),media_type="application/pdf",
        headers={"Content-Disposition":'attachment; filename="agreement-to-sign.pdf"',
                 "Cache-Control":"no-store","X-Document-SHA256":doc.sha256})

@router.post("/agreements/{agreement_id}/ukep/sign")
async def ukep_sign(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    agreement,side=signing_context(db,user,agreement_id)
    form=await request.form()
    require_csrf(request,form)
    signature=str(form.get("signature_base64",""))
    claimed_hash=str(form.get("document_sha256",""))
    doc=target_document(db,agreement_id)
    raw=get_file(doc.object_key)
    digest=hashlib.sha256(raw).hexdigest()
    if claimed_hash!=digest:raise HTTPException(409,"Документ изменился, повторите подписание")
    if db.scalar(select(QualifiedSignature.id).where(
        QualifiedSignature.agreement_id==agreement.id,QualifiedSignature.signer_side==side)):
        raise HTTPException(409,"Эта сторона уже подписала договор")
    previous=db.scalar(select(QualifiedSignature).where(QualifiedSignature.agreement_id==agreement.id).limit(1))
    if previous and (previous.attachment_id!=doc.id or previous.document_sha256!=digest):
        raise HTTPException(409,"Документ отличается от уже подписанного")
    try:
        verified=verify_detached_cades(raw,signature,side)
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(422,str(exc))
    except Exception:
        raise HTTPException(503,"Сервис проверки УКЭП недоступен")
    signature_bytes=base64.b64decode(signature,validate=True)
    key=save_file(agreement.id,"qualified_signature.sig",signature_bytes,"application/pkcs7-signature")
    db.add(QualifiedSignature(agreement_id=agreement.id,attachment_id=doc.id,
        signed_by_id=user.id,signer_side=side,document_sha256=digest,
        signature_object_key=key,**verified))
    db.flush()
    log_event(db,user,"ukep_verified","agreement",agreement.id,
        "Криптографическая проверка УКЭП пройдена: "+side+
        "; сертификат "+verified["certificate_serial"]+"; SHA256 "+digest)
    signed_sides=set(db.scalars(select(QualifiedSignature.signer_side).where(
        QualifiedSignature.agreement_id==agreement.id)).all())
    if signed_sides=={"UNIVERSITY","ENTERPRISE"}:
        agreement.status="COMPLETED"
        agreement.completed_at=datetime.utcnow()
        log_event(db,user,"both_qualified_signatures_verified","agreement",agreement.id,
                  "Обе стороны подписали один и тот же PDF проверенными УКЭП")
    db.commit()
    return {"ok":True,"status":agreement.status,"certificate":verified["certificate_subject"]}

@router.get("/agreements/{agreement_id}/ukep/signatures/{signature_id}/download")
def ukep_download(request:Request,agreement_id:int,signature_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    agreement=db.get(Agreement,agreement_id)
    if not agreement or not can_view_agreement(user,agreement):
        raise HTTPException(403,"Нет доступа")
    item=db.get(QualifiedSignature,signature_id)
    if not item or item.agreement_id!=agreement_id:raise HTTPException(404,"Подпись не найдена")
    return Response(get_file(item.signature_object_key),
        media_type="application/pkcs7-signature",
        headers={"Content-Disposition":f'attachment; filename="signature-{item.id}.sig"',
                 "Cache-Control":"private, no-store"})
