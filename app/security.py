"""Passwords, CSRF, roles and access scope. Non-bypassable checks live server-side."""
import hmac
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import HTTPException
from .models import User, Agreement

password_hasher=PasswordHasher(time_cost=3, memory_cost=32768, parallelism=2)
ROLE_LABELS={
 "STUDENT":"Студент",
 "STAFF":"Сотрудник университета",
 "COORDINATOR":"Ответственный за практику",
 "OFFICE":"Отдел практик и стажировок",
 "ENTERPRISE":"Предприятие",
 "MANAGER":"Ответственный по практике",
}
STAFF_ROLES={"STAFF","COORDINATOR","OFFICE","MANAGER"}
MANAGE_ROLES={"COORDINATOR","OFFICE","MANAGER"}

def hash_password(value:str)->str:
    return password_hasher.hash(value)

def verify_password(h:str, candidate:str)->bool:
    try:
        return password_hasher.verify(h,candidate)
    except (VerifyMismatchError,VerificationError,ValueError):
        return False

def validate_password(pwd:str)->bool:
    return len(pwd)>=12 and any(c.isupper() for c in pwd) and any(c.islower() for c in pwd) and any(c.isdigit() for c in pwd)

def csrf_token(request):
    if "csrf_token" not in request.session:
        request.session["csrf_token"]=secrets.token_urlsafe(32)
    return request.session["csrf_token"]

def require_csrf(request, form):
    supplied=str(form.get("csrf_token", ""))
    if not hmac.compare_digest(supplied, str(request.session.get("csrf_token", "---"))):
        raise HTTPException(status_code=403,detail="Недействительный защитный токен формы. Обновите страницу.")

def current_user(request, db):
    uid=request.session.get("uid")
    return db.get(User, uid) if uid is not None else None

def require_user(request, db):
    user=current_user(request,db)
    if not user or not user.is_active:
        raise HTTPException(status_code=401,detail="Для работы необходимо войти в систему")
    return user

def require_role(user, roles):
    if user.role not in set(roles):
        raise HTTPException(status_code=403,detail="Действие недоступно вашей роли")

def can_view_agreement(user:User, agreement:Agreement)->bool:
    if user.role in STAFF_ROLES:
        return True
    if user.role=="STUDENT":
        return user.id==agreement.student_id and not agreement.is_demo
    if user.role=="ENTERPRISE":
        return user.id==agreement.enterprise_user_id
    return False

def can_upload_agreement(user:User, agreement:Agreement)->bool:
    if not can_view_agreement(user,agreement) or agreement.status=="COMPLETED":
        return False
    if user.role=="STUDENT":
        return agreement.student_id==user.id and agreement.status in {"DRAFT","REVISION_REQUIRED"}
    if user.role=="ENTERPRISE":
        return agreement.enterprise_user_id==user.id and agreement.status=="PARTNER_REVIEW"
    return user.role in STAFF_ROLES