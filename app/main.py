"""Цифра — working role-based document workflow MVP.

Run: uvicorn app.main:app --reload
This implements *internal electronic approval*, not qualified electronic signatures.
"""
import csv
import os
import hashlib
import io
import math
import secrets
import statistics
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, desc, func, or_, and_
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .settings import settings, ROOT
from .models import User, Agreement, Attachment, Task, AuditEvent, PartnerInvite, AgreementNote, BaselineObservation, LoginAttempt, CompanyCard, QualifiedSignature, DocumentTemplate
from .security import (ROLE_LABELS, STAFF_ROLES, MANAGE_ROLES, hash_password, verify_password,
                       current_user, require_user, require_role, can_view_agreement, can_upload_agreement,
                       csrf_token, require_csrf, validate_password)
from .services import (STATUS_LABELS, STATUS_STEPS, ACTIONS_LABELS, log_event,
                       possible_actions,advance_agreement)
from .storage import save_file, get_file, validate_file, safe_filename

app=FastAPI(title="Цифра — ЭДО практики",docs_url=None,redoc_url=None,openapi_url=None)
from .ukep_routes import router as ukep_router
app.include_router(ukep_router)
allowed_hosts=[x.strip() for x in settings.ALLOWED_HOSTS.split(",") if x.strip()]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    allowed_hosts.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts)
app.add_middleware(SessionMiddleware,secret_key=settings.SECRET_KEY,https_only=settings.SESSION_HTTPS_ONLY,
                   same_site="lax", max_age=24*60*60,session_cookie="cifra_session")
app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
templates=Jinja2Templates(directory=str(ROOT/"templates"))
templates.env.filters["date_ru"]=lambda x: x.strftime("%d.%m.%Y") if x else "—"
templates.env.filters["datetime_ru"]=lambda x: x.strftime("%d.%m.%Y · %H:%M") if x else "—"
templates.env.filters["size_kb"]=lambda x:f"{x/1024:.0f} КБ" if x else "0 КБ"
def display_updated_date(case):
    # Demo-only display dates; actual database audit timestamps are untouched.
    if case.is_demo:
        return (datetime(2026,11,17)+timedelta(days=(int(case.id)-1)%8)).strftime("%d.%m.%Y")
    return case.updated_at.strftime("%d.%m.%Y") if case.updated_at else "—"

templates.env.filters["display_updated_date"]=display_updated_date

@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    seed_demo_tasks()

def seed_demo_tasks():
    """Idempotent training assignments for demo users and practice-office staff."""
    from .database import SessionLocal
    with SessionLocal() as db:
        users=db.scalars(select(User).where(User.is_active.is_(True))).all()
        demo_cases=db.scalars(select(Agreement).where(Agreement.is_demo.is_(True)).order_by(Agreement.id).limit(12)).all()
        for user in users:
            is_demo=str(user.username or "").lower().startswith(("demo","test"))
            is_practice_office=(user.role=="OFFICE" or "отдел практик" in str(user.full_name or "").lower())
            if not (is_demo or is_practice_office):
                continue
            if is_practice_office and demo_cases:
                labels=[
                    ("[Учебное] Проверить реквизиты и приложения","Проверить сведения о профильной организации, помещениях и приложениях № 1 и № 2."),
                    ("[Учебное] Согласовать договор практики","Проверить содержание и направить на следующий этап согласования."),
                    ("[Учебное] Подготовить договор к электронной подписи","Проверить итоговый PDF и наличие мест подписания обеих сторон.")
                ]
                for index,case in enumerate(demo_cases):
                    title,details=labels[index%len(labels)]
                    unique_title=title+" № "+str(case.id)
                    exists=db.scalar(select(Task.id).where(Task.assigned_to_id==user.id,
                                    Task.agreement_id==case.id,Task.title==unique_title))
                    if exists:continue
                    db.add(Task(title=unique_title,details=details,created_by_id=user.id,
                                assigned_to_id=user.id,agreement_id=case.id,
                                due_at=datetime(2026,11,25)+timedelta(days=index%6),
                                priority="high" if index%3==2 else "normal",status="TODO"))
            elif is_demo:
                existing=db.scalar(select(func.count(Task.id)).where(Task.assigned_to_id==user.id))
                if existing:continue
                labels=[
                    ("[Учебное] Проверить реквизиты договора","Проверить стороны, контактные данные, адреса и приложения."),
                    ("[Учебное] Согласовать договор","Проверить документ и направить замечания либо согласовать."),
                    ("[Учебное] Подготовить электронное подписание","Проверить окончательный PDF и доступность сертификата УКЭП.")
                ]
                for index,(title,details) in enumerate(labels):
                    db.add(Task(title=title,details=details,created_by_id=user.id,
                                assigned_to_id=user.id,due_at=datetime(2026,11,25)+timedelta(days=index),
                                priority="high" if index==2 else "normal",status="TODO"))
        db.commit()

@app.middleware("http")
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="SAMEORIGIN"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"]="private, no-store"
    if settings.production:
        response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    return response

@app.exception_handler(401)
async def unauthorized_handler(request,exc):
    return RedirectResponse("/login?next="+quote(request.url.path,safe="/"),status_code=303)

@app.exception_handler(403)
async def forbidden_handler(request,exc):
    return templates.TemplateResponse(request,"error.html",{"title":"Недостаточно прав","message":str(exc.detail),"user":None,"role_labels":ROLE_LABELS,"status_labels":STATUS_LABELS,"csrf_token":csrf_token(request),"flash":None},status_code=403)

@app.exception_handler(422)
async def validation_handler(request,exc):
    # Show an understandable validation error instead of raw JSON.
    return templates.TemplateResponse(request,"error.html",
        {"title":"Проверьте данные","message":str(exc.detail),
         "user":None,"role_labels":ROLE_LABELS,"status_labels":STATUS_LABELS,
         "csrf_token":csrf_token(request),"flash":None},status_code=422)

@app.exception_handler(404)
async def not_found_handler(request,exc):
    return templates.TemplateResponse(request,"error.html",{"title":"Страница не найдена","message":"Проверьте адрес или вернитесь к списку документов.","user":None,"role_labels":ROLE_LABELS,"status_labels":STATUS_LABELS,"csrf_token":csrf_token(request),"flash":None},status_code=404)


EVENT_LABELS={
"status_changed":"Изменён этап согласования",
"company_card_created":"Карточка предприятия заполнена",
"company_card_edited":"Карточка предприятия обновлена",
"file_uploaded":"Загружен документ",
"note_added":"Добавлен комментарий",
"partner_invited":"Приглашено предприятие",
"partner_activated":"Предприятие подключено",
"existing_partner_attached":"Договор закреплён за предприятием",
"ukep_verified":"Электронная подпись проверена",
"both_qualified_signatures_verified":"Договор подписан обеими сторонами",
"agreement_created":"Договор зарегистрирован",
"task_created":"Создано поручение",
"task_status_changed":"Изменён статус поручения",
}
def ctx(request,db,user=None,active="",**kwargs):
    if user is None: user=current_user(request,db)
    flash=request.session.pop("flash",None)
    return {"user":user,"active":active,"role_labels":ROLE_LABELS,"status_labels":STATUS_LABELS,"event_labels":EVENT_LABELS,
            "csrf_token":csrf_token(request),"demo_mode":settings.DEMO_MODE,"flash":flash,
            "today":datetime.utcnow(),**kwargs}

def render(request,db,template,user=None,active="",**kwargs):
    return templates.TemplateResponse(request,template,ctx(request,db,user,active,**kwargs))

def notify(request,message,kind="success"):
    request.session["flash"]={"message":message,"kind":kind}

def go(location="/dashboard"):
    return RedirectResponse(location,status_code=303)

def as_date(value):
    try: return datetime.strptime(value,"%Y-%m-%d") if value else None
    except ValueError: return None

def scoped_agreements(db,user):
    # Staff must see real submitted agreements alongside the 29 demo cases.
    # Student access is always restricted to their own agreements.
    stmt=select(Agreement)
    if user.role=="STUDENT":
        stmt=stmt.where(Agreement.student_id==user.id)
    elif user.role=="ENTERPRISE":
        stmt=stmt.where(Agreement.enterprise_user_id==user.id)
    return stmt

def read_case(db,user,agreement_id):
    case=db.get(Agreement,agreement_id)
    if not case: raise HTTPException(404,"Договор не найден")
    if not can_view_agreement(user,case):raise HTTPException(403,"Нет доступа к этому договору")
    return case

def list_tasks(db,user):
    q=select(Task).where(Task.assigned_to_id==user.id)
    if user.role in {"STUDENT","ENTERPRISE"}:
        q=q.outerjoin(Agreement,Task.agreement_id==Agreement.id).where(
            or_(Task.agreement_id.is_(None),Agreement.is_demo.is_(False)))
    return db.scalars(q.order_by(Task.due_at,desc(Task.id))).all()

@app.get("/",response_class=HTMLResponse)
def index(request:Request,db:Session=Depends(get_db)):
    if current_user(request,db):return go("/dashboard")
    return render(request,db,"landing.html",active="home")

@app.get("/login",response_class=HTMLResponse)
def login(request:Request,db:Session=Depends(get_db)):
    if current_user(request,db):return go("/dashboard")
    return render(request,db,"login.html",title="Войти в Цифру",next_url=request.query_params.get("next","/dashboard"))

@app.post("/login")
async def login_post(request:Request,db:Session=Depends(get_db)):
    form=await request.form();require_csrf(request,form)
    username=str(form.get("username","")).strip().lower()[:90]
    password=str(form.get("password",""))
    realm=str(form.get("realm",""))
    next_url=str(form.get("next","/dashboard"))
    if not next_url.startswith("/") or next_url.startswith("//") or "\\" in next_url:next_url="/dashboard"
    if "?" in next_url:next_url=next_url.split("?",1)[0]
    ip=request.client.host if request.client else ""
    cutoff=datetime.utcnow()-timedelta(minutes=15)
    failed=db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.username==username,
                   LoginAttempt.at>=cutoff)) or 0
    if failed>=8:
        notify(request,"Слишком много неудачных попыток входа. Повторите через 15 минут.","error")
        return go("/login")
    user=db.scalar(select(User).where(User.username==username,User.is_active.is_(True)))
    if not user or not verify_password(user.password_hash,password) or (realm and user.role!=realm):
        db.add(LoginAttempt(username=username,remote_address=ip));db.commit()
        notify(request,"Неверный логин, пароль или тип кабинета","error")
        return go("/login")
    db.query(LoginAttempt).filter(LoginAttempt.username==username).delete(synchronize_session=False)
    db.commit()
    request.session.clear();request.session["uid"]=user.id
    csrf_token(request)
    notify(request,f"Добро пожаловать, {user.full_name}!")
    return go(next_url)

@app.post("/logout")
async def logout(request:Request):
    form=await request.form();require_csrf(request,form)
    request.session.clear()
    return go("/")

@app.get("/dashboard",response_class=HTMLResponse)
def dashboard(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    cases=db.scalars(scoped_agreements(db,user).order_by(desc(Agreement.updated_at)).limit(7)).all()
    if user.role=="STUDENT":
        return render(request,db,"student_home.html",user,active="dashboard",cases=cases)
    if user.role=="ENTERPRISE":
        return render(request,db,"enterprise_home.html",user,active="dashboard",cases=cases)
    visible=scoped_agreements(db,user)
    total=db.scalar(select(func.count()).select_from(visible.subquery())) or 0
    done=db.scalar(select(func.count()).select_from(visible.where(Agreement.status=="COMPLETED").subquery())) or 0
    active=total-done
    stage_counts=db.execute(select(Agreement.status,func.count(Agreement.id)).where(Agreement.is_demo.is_(False)).group_by(Agreement.status)).all()
    tasks=list_tasks(db,user)
    open_tasks=[t for t in tasks if t.status!="DONE"]
    overdue=[t for t in open_tasks if t.due_at and t.due_at<datetime.utcnow()]
    events=[]
    if user.role in STAFF_ROLES:
        events=db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(8)).all()
    elif user.role=="STUDENT":
        own=[c.id for c in cases]
        if own:events=db.scalars(select(AuditEvent).where(AuditEvent.entity_type=="agreement",AuditEvent.entity_id.in_(own)).order_by(desc(AuditEvent.id)).limit(7)).all()
    else:
        own=[c.id for c in cases]
        if own:events=db.scalars(select(AuditEvent).where(AuditEvent.entity_type=="agreement",AuditEvent.entity_id.in_(own)).order_by(desc(AuditEvent.id)).limit(7)).all()
    return render(request,db,"dashboard.html",user,active="dashboard",cases=cases,open_tasks=open_tasks[:5],approval_items=signing_approval_items(db,user)[:8],overdue=len(overdue),total=total,done=done,active_count=active,events=events,stage_counts=stage_counts)

def signing_approval_items(db,user):
    """Live actionable inbox entries, derived from agreement workflow state."""
    if user.role=="STUDENT":
        return []
    cases=db.scalars(scoped_agreements(db,user).where(Agreement.status.notin_(["COMPLETED","REJECTED"])).order_by(desc(Agreement.updated_at)).limit(100)).all()
    items=[]
    for case in cases:
        status=case.status or ""
        if status=="SIGNING_PENDING":
            action="Подписать электронной подписью"
        elif status in {"DRAFT","REVISION_REQUIRED"}:
            continue
        else:
            action="Согласовать договор"
        items.append({"title":action+" № "+str(case.id),
                      "agreement_id":case.id,"status":status,
                      "company":case.company_name})
    return items


@app.get("/agreements",response_class=HTMLResponse)
def agreements(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    status=request.query_params.get("status","")
    q=scoped_agreements(db,user)
    if status in STATUS_LABELS:q=q.where(Agreement.status==status)
    search=request.query_params.get("q","").strip()[:120]
    if search:
        student_matches=select(User.id).where(User.full_name.ilike(f"%{search}%"))
        card_matches=select(CompanyCard.agreement_id).where(CompanyCard.student_name.ilike(f"%{search}%"))
        q=q.where(or_(Agreement.reference.ilike(f"%{search}%"),
                     Agreement.title.ilike(f"%{search}%"),
                     Agreement.company_name.ilike(f"%{search}%"),
                     Agreement.student_id.in_(student_matches),
                     Agreement.id.in_(card_matches)))
    cases=db.scalars(q.order_by(Agreement.is_demo.asc(),desc(Agreement.created_at)).limit(200)).all()
    return render(request,db,"agreements.html",user,active="agreements",cases=cases,filter_status=status,q=search,status_items=STATUS_LABELS.items())

@app.get("/agreements/new",response_class=HTMLResponse)
def new_agreement(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role not in {"STAFF","COORDINATOR","OFFICE"}:
        raise HTTPException(403,"Нельзя создать документ")
    students=db.scalars(select(User).where(User.role=="STUDENT",User.is_active.is_(True)).order_by(User.full_name)).all() if user.role in STAFF_ROLES else []
    return render(request,db,"agreement_new.html",user,active="agreements",students=students)

@app.post("/agreements/new")
async def new_agreement_post(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,{"STAFF","COORDINATOR","OFFICE"})
    f=await request.form();require_csrf(request,f)
    title=str(f.get("title","")).strip()[:230]
    company=str(f.get("company_name","")).strip()[:210]
    if len(title)<5 or len(company)<2:
        notify(request,"Укажите название документа и предприятие","error");return go("/agreements/new")
    practice_type=str(f.get("practice_type","Производственная"))
    if practice_type not in ["Производственная","Учебная","Преддипломная"]:practice_type="Производственная"
    student_id=user.id
    if user.role in STAFF_ROLES:
        try: student_id=int(f.get("student_id") or 0)
        except ValueError:student_id=0
        owner=db.get(User,student_id)
        if not owner or owner.role!="STUDENT":
            notify(request,"Выберите студента","error");return go("/agreements/new")
    order=str(f.get("signing_order","university_first"))
    if order not in {"university_first","enterprise_first"}:order="university_first"
    obj=Agreement(reference="TMP-"+secrets.token_hex(6).upper(),title=title,student_id=student_id,company_name=company,
                  practice_type=practice_type,practice_start=str(f.get("practice_start", ""))[:10],practice_end=str(f.get("practice_end", ""))[:10],
                  deadline_at=as_date(str(f.get("deadline_at",""))),signing_order=order,
                  description=str(f.get("description",""))[:3000])
    db.add(obj);db.flush()
    obj.reference=f"CF-{datetime.utcnow().year}-{obj.id:05d}"
    log_event(db,user,"created","agreement",obj.id,"Создан черновик договора практики")
    db.commit();notify(request,"Черновик создан. Загрузите договор и отправьте его на проверку.")
    return go(f"/agreements/{obj.id}")

@app.get("/agreements/{agreement_id}",response_class=HTMLResponse)
def agreement_detail(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);obj=read_case(db,user,agreement_id)
    files=db.scalars(select(Attachment).where(Attachment.agreement_id==obj.id).order_by(desc(Attachment.created_at))).all()
    events=db.scalars(select(AuditEvent).where(AuditEvent.entity_type=="agreement",AuditEvent.entity_id==obj.id).order_by(desc(AuditEvent.created_at),desc(AuditEvent.id))).all()
    notes=db.scalars(select(AgreementNote).where(AgreementNote.agreement_id==obj.id).order_by(desc(AgreementNote.created_at))).all()
    tasks=db.scalars(select(Task).where(Task.agreement_id==obj.id).order_by(Task.due_at)).all()
    if user.role in {"STUDENT","ENTERPRISE"}:tasks=[t for t in tasks if t.assigned_to_id==user.id]
    for e in events:
        if not hasattr(e,"actor_name"):
            e.actor_name=(db.get(User,e.actor_id).full_name if e.actor_id and db.get(User,e.actor_id) else "Система")
    display_steps=(STATUS_STEPS if obj.signing_order=="university_first" else ["DRAFT","STAFF_REVIEW","COORDINATOR_REVIEW","PARTNER_REVIEW","OFFICE_REVIEW","COMPLETED"])
    # УКЭП is part of the final organization's approval stage, not a separate route step.
    display_steps=[step for step in display_steps if step!="SIGNING_PENDING"]
    visible_status=("PARTNER_REVIEW" if obj.signing_order=="university_first" else "OFFICE_REVIEW") if obj.status=="SIGNING_PENDING" else obj.status
    step_index=display_steps.index(visible_status) if visible_status in display_steps else 0
    has_card=db.scalar(select(CompanyCard.id).where(CompanyCard.agreement_id==obj.id).limit(1)) is not None
    pdf_files=[f for f in files if f.filename.lower().endswith(".pdf")]
    preview_url=(f"/agreements/{obj.id}/preview-pdf" if has_card else (f"/files/{pdf_files[0].id}/preview" if pdf_files else None))
    signatures=db.scalars(select(QualifiedSignature).where(QualifiedSignature.agreement_id==obj.id).order_by(QualifiedSignature.created_at)).all()
    can_ukep=(obj.status=="SIGNING_PENDING" and ((user.role=="OFFICE") or (user.role=="ENTERPRISE" and obj.enterprise_user_id==user.id)) and not any(x.signer_side==("UNIVERSITY" if user.role=="OFFICE" else "ENTERPRISE") for x in signatures))
    return render(request,db,"agreement_detail.html",user,active="agreements",case=obj,files=files,events=events,notes=notes,tasks=tasks,preview_url=preview_url,has_card=has_card,signatures=signatures,can_ukep=can_ukep,ukep_configured=bool(settings.UKEP_VERIFY_URL and settings.UKEP_VERIFY_TOKEN),
                  allowed_actions=possible_actions(user,obj),actions_labels=ACTIONS_LABELS,
                  allow_upload=(obj.status not in {"SIGNING_PENDING","COMPLETED"} and not signatures and can_upload_agreement(user,obj)),can_invite=(user.role in STAFF_ROLES and obj.enterprise_user_id is None and obj.status!="COMPLETED"),
                  step_names=display_steps,step_index=step_index)

@app.post("/agreements/{agreement_id}/transition")
async def agreement_transition(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);obj=read_case(db,user,agreement_id)
    f=await request.form();require_csrf(request,f)
    action=str(f.get("action",""))
    advance_agreement(db,user,obj,action)
    db.commit()
    from .integrations import notify_transition
    notify_transition(db,obj)
    notify(request,"Статус согласования обновлён; событие записано в историю.")
    return go(f"/agreements/{obj.id}")

@app.post("/agreements/{agreement_id}/upload")
async def agreement_upload(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);obj=read_case(db,user,agreement_id)
    if obj.status in {"SIGNING_PENDING","COMPLETED"} or db.scalar(select(QualifiedSignature.id).where(QualifiedSignature.agreement_id==obj.id).limit(1)):
        raise HTTPException(409,"После передачи на УКЭП изменение документа запрещено")
    if not can_upload_agreement(user,obj):raise HTTPException(403,"Загрузка недоступна на этом этапе")
    f=await request.form();require_csrf(request,f)
    upload=f.get("file")
    if not isinstance(upload,UploadFile):
        # starlette UploadFile class may differ in FastAPI runtime
        if not hasattr(upload,"read"):
            notify(request,"Выберите файл","error");return go(f"/agreements/{agreement_id}")
    data=await upload.read(settings.MAX_UPLOAD_MB*1024*1024+1)
    try:ext,mime=validate_file(upload.filename or "",data)
    except ValueError as e:
        notify(request,str(e),"error");return go(f"/agreements/{agreement_id}")
    key=save_file(obj.id,upload.filename,data,mime)
    version=(db.scalar(select(func.max(Attachment.version_no)).where(Attachment.agreement_id==obj.id)) or 0)+1
    attachment=Attachment(agreement_id=obj.id,uploaded_by_id=user.id,filename=safe_filename(upload.filename or "документ"),
                          object_key=key,mime_type=mime,size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),version_no=version)
    db.add(attachment)
    log_event(db,user,"file_uploaded","agreement",obj.id,f"Версия {version}: {attachment.filename} · SHA-256 {attachment.sha256[:16]}…")
    db.commit();notify(request,"Файл загружен в защищённое хранилище; версия и контрольная сумма сохранены.")
    return go(f"/agreements/{agreement_id}")

@app.get("/files/{attachment_id}/preview")
def preview_attachment(request:Request,attachment_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    attachment=db.get(Attachment,attachment_id)
    if not attachment:raise HTTPException(404,"Документ не найден")
    read_case(db,user,attachment.agreement_id)
    if not attachment.filename.lower().endswith(".pdf"):
        raise HTTPException(415,"Встроенный просмотр доступен для PDF")
    return Response(get_file(attachment.object_key),media_type="application/pdf",
        headers={"Content-Disposition":"inline","Cache-Control":"private, no-store",
                 "Content-Security-Policy":"sandbox allow-same-origin"})

@app.get("/files/{attachment_id}/download")
def download_file(request:Request,attachment_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    f=db.get(Attachment,attachment_id)
    if not f:raise HTTPException(404,"Файл не найден")
    read_case(db,user,f.agreement_id)
    raw=get_file(f.object_key)
    safe=f.filename.replace('"','_').replace("\r","").replace("\n","")
    from urllib.parse import quote as uquote
    return Response(raw,media_type="application/octet-stream",headers={"Content-Disposition":f"attachment; filename*=UTF-8''{uquote(safe)}","X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store"})

@app.post("/agreements/{agreement_id}/note")
async def new_note(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);obj=read_case(db,user,agreement_id)
    f=await request.form();require_csrf(request,f)
    message=str(f.get("message","")).strip()[:2000]
    if message:
        db.add(AgreementNote(agreement_id=obj.id,author_id=user.id,text=message))
        log_event(db,user,"note_added","agreement",obj.id,"Добавлен комментарий к договору")
        db.commit();notify(request,"Комментарий добавлен")
    return go(f"/agreements/{agreement_id}#discussion")

@app.get("/tasks",response_class=HTMLResponse)
def tasks(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role=="STUDENT":raise HTTPException(403,"Задачи доступны через карточку своего договора")
    all_tasks=list_tasks(db,user)
    users=db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all() if user.role in STAFF_ROLES else []
    cases=db.scalars(scoped_agreements(db,user).order_by(desc(Agreement.id)).limit(120)).all() if user.role in STAFF_ROLES else []
    return render(request,db,"tasks.html",user,active="tasks",tasks=all_tasks,approval_items=signing_approval_items(db,user),users=users,cases=cases,can_create=user.role in STAFF_ROLES)

@app.post("/tasks/new")
async def task_create(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,STAFF_ROLES)
    f=await request.form();require_csrf(request,f)
    title=str(f.get("title","")).strip()[:230]
    if len(title)<4:notify(request,"Укажите задачу","error");return go("/tasks")
    try:recipient_id=int(f.get("assigned_to_id") or 0)
    except ValueError:recipient_id=0
    recipient=db.get(User,recipient_id)
    if not recipient or not recipient.is_active:
        notify(request,"Выберите исполнителя","error");return go("/tasks")
    case_id=None
    try:case_id=int(f.get("agreement_id") or 0) or None
    except ValueError:pass
    if recipient.role in {"ENTERPRISE","STUDENT"}:
        case=db.get(Agreement,case_id) if case_id else None
        if not case or (case.enterprise_user_id if recipient.role=="ENTERPRISE" else case.student_id)!=recipient.id:
            notify(request,"Задачи назначаются только по договору этого пользователя","error");return go("/tasks")
    pr=str(f.get("priority","normal"))
    if pr not in {"low","normal","high"}:pr="normal"
    d=as_date(str(f.get("due_at","")))
    t=Task(title=title,details=str(f.get("details",""))[:3000],created_by_id=user.id,assigned_to_id=recipient.id,
           agreement_id=case_id,due_at=d,priority=pr)
    db.add(t);db.flush();log_event(db,user,"task_created","task",t.id,f"Назначено: {recipient.username}; срок {d.date().isoformat() if d else 'без срока'}")
    db.commit();notify(request,"Задача создана и назначена")
    return go("/tasks")

@app.post("/tasks/{task_id}/state")
async def task_state(request:Request,task_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);t=db.get(Task,task_id)
    if not t:raise HTTPException(404,"Задача не найдена")
    if user.id!=t.assigned_to_id and user.role not in STAFF_ROLES: raise HTTPException(403,"Нет доступа")
    if user.role in {"STUDENT","ENTERPRISE"}:
        linked=db.get(Agreement,t.agreement_id) if t.agreement_id else None
        if not linked or not can_view_agreement(user,linked):raise HTTPException(403,"Нет доступа")
    f=await request.form();require_csrf(request,f)
    state=str(f.get("state",""))
    if state not in {"TODO","IN_PROGRESS","DONE"}:raise HTTPException(422,"Недопустимый статус")
    old=t.status;t.status=state
    log_event(db,user,"task_updated","task",t.id,f"{old} → {state}")
    db.commit();notify(request,"Статус задачи обновлён")
    return go("/tasks")

@app.get("/activity",response_class=HTMLResponse)
def activity(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,STAFF_ROLES)
    events=db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(250)).all()
    for e in events: e.actor_name=(db.get(User,e.actor_id).full_name if e.actor_id and db.get(User,e.actor_id) else "Система")
    return render(request,db,"activity.html",user,active="activity",events=events)

@app.get("/analytics",response_class=HTMLResponse)
def analytics(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,STAFF_ROLES)
    observations=db.scalars(select(BaselineObservation)).all()
    total=len(observations)
    caldays=[x.campus_days for x in observations]
    evidence=Counter(o.evidence for o in observations)
    lost=sum(bool(x.lost) for x in observations)
    visits=sum(x.visits for x in observations)
    returns=sum(x.returns for x in observations)
    bucket_labels=["до 7","8–14","15–21","22–28","29+"]
    buckets=[0]*5
    for v in caldays:buckets[0 if v<=7 else 1 if v<=14 else 2 if v<=21 else 3 if v<=28 else 4]+=1
    completed=db.scalars(select(Agreement).where(Agreement.completed_at.is_not(None),Agreement.submitted_at.is_not(None),Agreement.is_demo.is_(False))).all()
    after_days=[(x.completed_at-x.submitted_at).total_seconds()/86400 for x in completed]
    live_total=db.scalar(select(func.count(Agreement.id)).where(Agreement.is_demo.is_(False))) or 0
    open_tasks=db.scalar(select(func.count(Task.id)).where(Task.status!="DONE")) or 0
    return render(request,db,"analytics.html",user,active="analytics",count=total,mean=statistics.mean(caldays) if caldays else 0,
                  median=statistics.median(caldays) if caldays else 0,confirmed=evidence.get("Документально подтверждено",0),
                  estimated=evidence.get("Экспертная оценка",0),lost=lost,visits=visits,returns=returns,
                  bucket_labels=bucket_labels,buckets=buckets,max_bucket=max(buckets) if buckets else 1,
                  after_count=len(after_days),after_mean=statistics.mean(after_days) if after_days else None,
                  live_total=live_total,open_tasks=open_tasks)

@app.get("/analytics/export")
def analytics_export(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,STAFF_ROLES)
    items=db.scalars(select(BaselineObservation).order_by(BaselineObservation.source_reference)).all()
    out=io.StringIO();w=csv.writer(out,delimiter=";")
    w.writerow(["ID","Вид практики","Сдан","Передан","Выдан","Дни вуза","Ожидание","Визиты","Возвраты","Утерян","Тип источника"])
    for x in items:w.writerow([x.source_reference,x.practice_type,x.submitted_date,x.forwarded_date,x.signed_date,x.campus_days,x.waiting_days,x.visits,x.returns,int(x.lost),x.evidence])
    raw="\ufeff"+out.getvalue()
    return Response(raw,media_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=cifra_baseline_anonymized.csv"})

@app.get("/team",response_class=HTMLResponse)
def team(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,{"MANAGER","OFFICE","COORDINATOR"})
    users=db.scalars(select(User).order_by(User.role,User.full_name)).all()
    return render(request,db,"team.html",user,active="team",users=users)

@app.post("/team/document-template")
async def upload_practice_template(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    require_role(user,{"MANAGER","OFFICE"})
    form=await request.form()
    require_csrf(request,form)
    uploaded=form.get("file")
    if not uploaded or not hasattr(uploaded,"read"):
        raise HTTPException(422,"Выберите типовой договор DOCX")
    filename=str(uploaded.filename or "")
    if not filename.lower().endswith(".docx"):
        raise HTTPException(422,"Допускается только DOCX")
    data=await uploaded.read(3_000_001)
    if not data.startswith(b"PK") or len(data)>3_000_000:
        raise HTTPException(422,"Некорректный файл DOCX")
    from docx import Document
    try:
        document=Document(io.BytesIO(data))
        source=" ".join(c.text for t in document.tables for row in t.rows for c in row.cells)
        for required in ("{{company_full_name}}","{{contract_number}}","{{student_name}}"):
            if required not in source:raise ValueError("Отсутствует поле "+required)
    except Exception as exc:
        raise HTTPException(422,"Неверная структура типового договора: "+str(exc))
    key=save_file(0,"practice_agreement.docx",data,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    item=DocumentTemplate(template_kind="practice_agreement",filename=safe_filename(filename),
        object_key=key,sha256=hashlib.sha256(data).hexdigest(),uploaded_by_id=user.id)
    db.add(item)
    log_event(db,user,"template_uploaded","template",0,
              "Установлен типовой договор; контрольная сумма "+item.sha256)
    db.commit()
    notify(request,"Типовой договор установлен. Новые документы формируются по его полному тексту.")
    return go("/team")

@app.post("/team/new")
async def new_user(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,{"MANAGER"})
    f=await request.form();require_csrf(request,f)
    username=str(f.get("username","")).strip().lower()[:90]
    full_name=str(f.get("full_name","")).strip()[:180]
    pwd=str(f.get("password", ""))
    role=str(f.get("role",""))
    if role not in ROLE_LABELS or role=="ENTERPRISE" or len(username)<4 or len(full_name)<3 or not validate_password(pwd):
        notify(request,"Проверьте роль, имя, логин и пароль (от 12 символов, цифра, верхний и нижний регистр)","error");return go("/team")
    if db.scalar(select(User.id).where(User.username==username)):
        notify(request,"Логин уже используется","error");return go("/team")
    newcomer=User(username=username,full_name=full_name,password_hash=hash_password(pwd),role=role)
    db.add(newcomer);db.flush();log_event(db,user,"user_created","user",newcomer.id,f"Роль: {role}")
    db.commit();notify(request,"Учётная запись создана")
    return go("/team")

@app.post("/team/pilot-credentials/download")
async def download_pilot_credentials(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    require_role(user,{"MANAGER"})
    form=await request.form()
    require_csrf(request,form)
    if form.get("confirm")!="yes":
        raise HTTPException(422,"Подтвердите создание тестовых доступов")
    import re
    from sqlalchemy import update
    output=io.StringIO()
    writer=csv.writer(output,delimiter=";")
    writer.writerow(["Роль","Предприятие / описание","Логин","Новый пароль","Договоров закреплено","Примечание"])
    demo_roles={
      "STUDENT":("student-demo","Студент"),
      "STAFF":("staff-demo","Сотрудник кафедры"),
      "COORDINATOR":("coordinator-demo","Ответственный за практику"),
      "OFFICE":("office-demo","Отдел практик"),
      "ENTERPRISE":("enterprise-demo","Предприятие"),
    }
    for role,(username,label) in demo_roles.items():
        password="P!"+secrets.token_urlsafe(18)+"7a"
        account=db.scalar(select(User).where(User.username==username))
        if account and account.role!=role:
            raise HTTPException(409,"Конфликт тестового логина: "+username)
        if account is None:
            account=User(username=username,full_name=label,role=role,
                         password_hash=hash_password(password),
                         organization_name="Организация" if role=="ENTERPRISE" else "")
            db.add(account)
        else:
            account.password_hash=hash_password(password)
            account.is_active=True
        writer.writerow([ROLE_LABELS.get(role,role),label,username,password,"","Тестовая роль, пароль обновлён"])
    db.flush()
    # One dedicated test login per distinct company that has a real,
    # unassigned agreement. Existing partner assignments are never changed.
    companies=db.scalars(select(Agreement.company_name).where(
        Agreement.is_demo.is_(False),Agreement.enterprise_user_id.is_(None)
    ).distinct().order_by(Agreement.company_name)).all()
    made=0
    for company in companies:
        if not company or not company.strip():continue
        if made>=250:break
        # Use a unique random suffix to prevent username collisions.
        username="pilot-org-"+secrets.token_hex(5)
        while db.scalar(select(User.id).where(User.username==username)):
            username="pilot-org-"+secrets.token_hex(5)
        password="P!"+secrets.token_urlsafe(18)+"7a"
        partner=User(username=username,full_name="Тестовый представитель: "+company[:145],
                     role="ENTERPRISE",organization_name=company[:200],
                     password_hash=hash_password(password))
        db.add(partner)
        db.flush()
        result=db.execute(update(Agreement).where(
            Agreement.company_name==company,
            Agreement.enterprise_user_id.is_(None),
            Agreement.is_demo.is_(False)
        ).values(enterprise_user_id=partner.id))
        writer.writerow(["Предприятие",company,username,password,result.rowcount or 0,
                         "Доступ только к закреплённым договорам"])
        made+=1
    log_event(db,user,"pilot_credentials_issued","user",user.id,
              f"Выданы новые тестовые пароли для 5 ролей и {made} предприятий")
    db.commit()
    payload="\ufeff"+output.getvalue()
    return Response(content=payload.encode("utf-8"),media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":'attachment; filename="cifra_pilot_credentials.csv"',
                 "Cache-Control":"no-store, no-cache, must-revalidate",
                 "Pragma":"no-cache","X-Content-Type-Options":"nosniff"})

@app.get("/partners",response_class=HTMLResponse)
def partners(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,MANAGE_ROLES)
    cases=db.scalars(select(Agreement).where(Agreement.status!="COMPLETED").order_by(desc(Agreement.id))).all()
    invites=db.scalars(select(PartnerInvite).order_by(desc(PartnerInvite.id)).limit(30)).all()
    return render(request,db,"partners.html",user,active="partners",cases=cases,invites=invites)

@app.post("/partners/invite")
async def create_partner_invite(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db);require_role(user,MANAGE_ROLES)
    f=await request.form();require_csrf(request,f)
    try:case_id=int(f.get("agreement_id") or 0)
    except ValueError:case_id=0
    case=db.get(Agreement,case_id)
    if not case or case.status=="COMPLETED" or case.enterprise_user_id:
        notify(request,"Выберите активный договор без назначенного партнёра","error");return go("/partners")
    # Organization identity is taken from the agreement, never from a free-text form.
    # A student cannot issue invitations; an authorized university employee must
    # verify the recipient through the company's independently established contact.
    if case.is_demo:
        notify(request,"Для этой записи приглашения не выдаются","error")
        return go("/partners")
    if db.scalar(select(PartnerInvite.id).where(
        PartnerInvite.agreement_id==case.id,
        PartnerInvite.redeemed_at.is_(None),
        PartnerInvite.expires_at>datetime.utcnow()).limit(1)):
        notify(request,"У этого договора уже есть действующее приглашение. Дождитесь активации или окончания срока.","error")
        return go("/partners")
    verified=str(f.get("recipient_verified",""))=="yes"
    if not verified:
        notify(request,"Подтвердите проверку официального контакта предприятия","error")
        return go("/partners")
    token=secrets.token_urlsafe(28)
    org=case.company_name
    invite=PartnerInvite(agreement_id=case.id,organization_name=org,token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                          created_by_id=user.id,expires_at=datetime.utcnow()+timedelta(hours=48))
    db.add(invite);db.flush();log_event(db,user,"partner_invite_created","agreement",case.id,f"Приглашение №{invite.id} для {case.company_name}; официальный контакт проверен сотрудником; действует 48 часов")
    db.commit()
    request.session["new_invite_url"]=settings.BASE_URL.rstrip("/")+f"/activate/{token}"
    notify(request,"Одноразовая ссылка создана. Передайте её партнёру по защищённому каналу.")
    return go("/partners")

@app.get("/activate/{token}",response_class=HTMLResponse)
def activate(request:Request,token:str,db:Session=Depends(get_db)):
    digest=hashlib.sha256(token.encode()).hexdigest()
    invite=db.scalar(select(PartnerInvite).where(PartnerInvite.token_sha256==digest))
    if not invite or invite.redeemed_at or invite.expires_at<datetime.utcnow():
        return render(request,db,"activation.html",title="Приглашение недействительно",valid=False)
    return render(request,db,"activation.html",title="Подключение предприятия",valid=True,company=invite.organization_name,token=token,existing_user=current_user(request,db))

@app.post("/activate/{token}")
async def activate_post(request:Request,token:str,db:Session=Depends(get_db)):
    existing=current_user(request,db)
    if existing and existing.role=="ENTERPRISE":
        raise HTTPException(409,"Используйте подключение к существующему кабинету предприятия")
    f=await request.form();require_csrf(request,f)
    digest=hashlib.sha256(token.encode()).hexdigest()
    invite=db.scalar(select(PartnerInvite).where(PartnerInvite.token_sha256==digest))
    if not invite or invite.redeemed_at or invite.expires_at<datetime.utcnow():raise HTTPException(403,"Приглашение недоступно")
    username=str(f.get("username","")).strip().lower()[:90]
    full_name=str(f.get("full_name","")).strip()[:180]
    pwd=str(f.get("password",""))
    if len(username)<4 or len(full_name)<3 or not validate_password(pwd):
        notify(request,"Заполните имя и придумайте надёжный пароль (12 символов, регистры и цифра)","error")
        return go(f"/activate/{token}")
    if db.scalar(select(User.id).where(User.username==username)):
        notify(request,"Логин занят","error");return go(f"/activate/{token}")
    obj=db.get(Agreement,invite.agreement_id)
    if not obj or obj.enterprise_user_id or obj.company_name!=invite.organization_name:
        raise HTTPException(403,"Договор уже закреплён или сведения предприятия изменены")
    u=User(username=username,full_name=full_name,role="ENTERPRISE",password_hash=hash_password(pwd),organization_name=invite.organization_name)
    db.add(u);db.flush();obj.enterprise_user_id=u.id;invite.redeemed_at=datetime.utcnow()
    log_event(db,u,"partner_activated","agreement",obj.id,f"Создан ограниченный доступ предприятия {invite.organization_name}")
    db.commit();notify(request,"Кабинет предприятия активирован. Войдите с созданными реквизитами.")
    return go("/login")

@app.post("/activate/{token}/existing")
async def activate_existing_enterprise(request:Request,token:str,db:Session=Depends(get_db)):
    """Attach an additional agreement to an already authenticated company account."""
    user=require_user(request,db)
    require_role(user,{"ENTERPRISE"})
    form=await request.form()
    require_csrf(request,form)
    digest=hashlib.sha256(token.encode()).hexdigest()
    invite=db.scalar(select(PartnerInvite).where(PartnerInvite.token_sha256==digest))
    if not invite or invite.redeemed_at or invite.expires_at<datetime.utcnow():
        raise HTTPException(403,"Приглашение недействительно")
    case=db.get(Agreement,invite.agreement_id)
    if not case or case.enterprise_user_id or case.company_name!=invite.organization_name:
        raise HTTPException(409,"Договор уже закреплён или организация изменена")
    if user.organization_name.strip().casefold()!=invite.organization_name.strip().casefold():
        raise HTTPException(403,"Ваш кабинет зарегистрирован на другое предприятие")
    case.enterprise_user_id=user.id
    invite.redeemed_at=datetime.utcnow()
    log_event(db,user,"existing_partner_attached","agreement",case.id,
              "Договор закреплён за ранее созданным кабинетом предприятия")
    db.commit()
    notify(request,"Договор добавлен в ваш кабинет")
    return go(f"/agreements/{case.id}")

@app.get("/modules",response_class=HTMLResponse)
def modules(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    require_role(user,STAFF_ROLES)
    return render(request,db,"modules.html",user,active="modules")

@app.get("/guide",response_class=HTMLResponse)
def guide(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    require_role(user,STAFF_ROLES)
    return render(request,db,"guide.html",user,active="guide")

@app.get("/profile",response_class=HTMLResponse)
def profile(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    return render(request,db,"profile.html",user,active="profile")

@app.post("/profile/password")
async def password_change(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    f=await request.form();require_csrf(request,f)
    old=str(f.get("old_password","")); new=str(f.get("new_password",""))
    if not verify_password(user.password_hash,old) or not validate_password(new):
        notify(request,"Проверьте текущий и новый пароль. Новый пароль: минимум 12 символов, верхний/нижний регистр и цифра.","error")
        return go("/profile")
    user.password_hash=hash_password(new)
    log_event(db,user,"password_changed","user",user.id,"Пароль учётной записи изменён")
    db.commit();request.session.clear()
    notify(request,"Пароль обновлён, войдите снова")
    return go("/login")

@app.get("/health")
def health():return {"status":"ok","service":"cifra"}
from .company_card import router as company_card_router
app.include_router(company_card_router)
