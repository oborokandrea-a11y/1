"""Optional outbound notifications and signed university event delivery.

No network calls occur unless credentials/endpoints are explicitly configured.
Delivery failures are logged without rolling back a committed agreement.
"""
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from .settings import settings

log=logging.getLogger(__name__)

def send_status_email(recipient, reference, status):
    if not (settings.SMTP_HOST and settings.SMTP_FROM and recipient):
        return False
    msg=EmailMessage()
    msg["From"]=settings.SMTP_FROM
    msg["To"]=recipient
    msg["Subject"]="Цифра: обновлён статус договора "+reference
    msg.set_content("Договор "+reference+"\nТекущий этап: "+status+
                    "\nОткройте личный кабинет: "+settings.BASE_URL.rstrip("/")+"/login"+
                    "\n\nЭто уведомление о внутреннем документообороте, не электронная подпись.")
    try:
        if settings.SMTP_SSL:
            with smtplib.SMTP_SSL(settings.SMTP_HOST,settings.SMTP_PORT,
                                  timeout=10,context=ssl.create_default_context()) as server:
                if settings.SMTP_USERNAME:server.login(settings.SMTP_USERNAME,settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST,settings.SMTP_PORT,timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:server.login(settings.SMTP_USERNAME,settings.SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception:
        log.exception("SMTP delivery failed for agreement %s",reference)
        return False

def send_university_event(reference,status,event_id):
    endpoint=settings.UNIVERSITY_WEBHOOK_URL
    secret=settings.UNIVERSITY_WEBHOOK_SECRET
    if not (endpoint and secret):
        return False
    parsed=urlparse(endpoint)
    if parsed.scheme!="https" or not parsed.hostname:
        log.error("University endpoint must use HTTPS")
        return False
    payload=json.dumps({"event":"agreement.status_changed","reference":reference,
                        "status":status,"event_id":event_id},
                       ensure_ascii=False,separators=(",",":")).encode("utf-8")
    signature=hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest()
    request=Request(endpoint,data=payload,method="POST",headers={
        "Content-Type":"application/json","X-Cifra-Signature-SHA256":signature,
        "X-Cifra-Event-ID":str(event_id)})
    try:
        with urlopen(request,timeout=8) as response:
            return 200<=response.status<300
    except Exception:
        log.exception("University webhook failed for agreement %s",reference)
        return False

def notify_transition(db,agreement):
    """Call only after DB commit; do not send emails for synthetic records."""
    if agreement.is_demo:return
    from .models import CompanyCard,User,AuditEvent
    from sqlalchemy import select,desc
    from .services import STATUS_LABELS
    card=db.scalar(select(CompanyCard).where(CompanyCard.agreement_id==agreement.id))
    recipients=set()
    if card and card.contact_email and "@" in card.contact_email:
        recipients.add(card.contact_email.strip())
    # User records currently have no verified email column.
    for address in recipients:
        send_status_email(address,agreement.reference,STATUS_LABELS.get(agreement.status,agreement.status))
    event=db.scalar(select(AuditEvent).where(AuditEvent.entity_type=="agreement",
        AuditEvent.entity_id==agreement.id).order_by(desc(AuditEvent.id)).limit(1))
    if event:send_university_event(agreement.reference,agreement.status,event.id)
