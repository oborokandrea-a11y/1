"""Company questionnaire and downloadable editable draft agreement (not a signature)."""
import io
import re
import secrets
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, update, text
from docx import Document
from docx.shared import Pt, Cm
from .database import get_db, SessionLocal
from .models import User, Agreement, CompanyCard, AgreementNumberSequence, DocumentTemplate
from .security import require_user, require_csrf, csrf_token, ROLE_LABELS
from .services import STATUS_LABELS, log_event
from .settings import ROOT
from fastapi.templating import Jinja2Templates

router=APIRouter()
views=Jinja2Templates(directory=str(ROOT/"templates"))
FIELDS = ("student_name","group_name","course_name","department","program","company_full_name","company_address",
"company_short_name","residency","registration_country","company_actual_address","company_phone","company_fax","bank_account","bank_name","correspondent_account","bank_bik","company_inn","company_kpp","company_ogrn","company_okpo","company_okved","director_position",
"director_name","director_basis","contact_name","contact_phone","contact_email",
"room_department","room_address","university_contact","university_phone","university_email")

def safe(v, limit=450):
    return re.sub(r"[\x00-\x1f]+", " ", str(v or "")).strip()[:limit]

def context(request, user, **kw):
    return dict(user=user,active="agreements",role_labels=ROLE_LABELS,status_labels=STATUS_LABELS,
        csrf_token=csrf_token(request),flash=None,**kw)

@router.get("/company-card/new")
def form(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role!="STUDENT":raise HTTPException(403,"Анкета доступна студенту")
    existing=db.scalar(select(Agreement).join(CompanyCard,CompanyCard.agreement_id==Agreement.id).where(Agreement.student_id==user.id,Agreement.is_demo.is_(False)).order_by(Agreement.id.desc()))
    if existing:return RedirectResponse(f"/agreements/{existing.id}/company-card",status_code=303)
    return views.TemplateResponse(request,"company_card.html",context(request,user,card=None))

@router.post("/company-card/new")
async def submit(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role!="STUDENT":raise HTTPException(403,"Анкета доступна студенту")
    existing=db.scalar(select(Agreement.id).join(CompanyCard,CompanyCard.agreement_id==Agreement.id).where(Agreement.student_id==user.id,Agreement.is_demo.is_(False)))
    if existing:raise HTTPException(409,"Карточка уже создана; повторное создание недоступно")
    f=await request.form();require_csrf(request,f)
    data={key:safe(f.get(key)) for key in FIELDS}
    for key in ("student_name","group_name","department","program","company_full_name","company_address",
                "company_inn","director_position","director_name","director_basis","contact_name",
                "contact_phone","room_address"):
        if not data[key]:raise HTTPException(422,"Не заполнено обязательное поле: "+key)
    inn=data["company_inn"]
    if not inn.isdigit() or len(inn) not in (10,12):
        raise HTTPException(422,"ИНН должен состоять из 10 или 12 цифр")
    start=safe(f.get("practice_start"),10)
    end=safe(f.get("practice_end"),10)
    try:
        if datetime.strptime(start,"%Y-%m-%d")>datetime.strptime(end,"%Y-%m-%d"):
            raise ValueError()
    except ValueError:
        raise HTTPException(422,"Укажите корректный период практики")
    practice=safe(f.get("practice_type"),70)
    if practice not in ("Производственная","Учебная","Преддипломная"):
        raise HTTPException(422,"Неверный вид практики")
    case=Agreement(reference="TEMP-"+secrets.token_hex(12),
        title="Договор о практической подготовке — "+data["company_full_name"][:140],
        student_id=user.id,company_name=data["company_full_name"][:210],
        practice_type=practice,practice_start=start,practice_end=end,
        status="DRAFT",signing_order="enterprise_first",description="Создано из анкеты предприятия. Черновик требует проверки университетом.")
    db.add(case);db.flush()
    # Monotonic database-generated identifier, independent of simultaneous submissions.
    # Official university numbering requires a verified starting point and approval.
    year=datetime.utcnow().year
    db.execute(text("INSERT INTO agreement_number_sequences (year, last_number) VALUES (:year, 1100) ON CONFLICT (year) DO NOTHING"),{"year":year})
    number=db.scalar(update(AgreementNumberSequence).where(AgreementNumberSequence.year==year).values(last_number=AgreementNumberSequence.last_number+1).returning(AgreementNumberSequence.last_number))
    if number is None:raise HTTPException(503,"Нумерация договоров не настроена")
    case.reference=f"{number}/{year % 100:02d}"
    card=CompanyCard(agreement_id=case.id,practice_paid=f.get("practice_paid")=="yes",**data)
    db.add(card);log_event(db,user,"company_card_created","agreement",case.id,"Студент заполнил карточку предприятия")
    db.commit()
    return RedirectResponse(f"/agreements/{case.id}/company-card",status_code=303)

@router.get("/agreements/{agreement_id}/company-card/edit")
def edit_card_form(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    case,card=get_case(db,user,agreement_id)
    if user.role!="STUDENT" or case.student_id!=user.id:
        raise HTTPException(403,"Редактирование доступно только владельцу карточки")
    if case.status not in {"DRAFT","REVISION_REQUIRED"}:
        return views.TemplateResponse(request,"company_card_view.html",
            context(request,user,case=case,card=card,edit_locked=True))
    return views.TemplateResponse(request,"company_card.html",
        context(request,user,card=card,case=case,editing=True))

@router.post("/agreements/{agreement_id}/company-card/edit")
async def edit_card_post(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db)
    case,card=get_case(db,user,agreement_id)
    if user.role!="STUDENT" or case.student_id!=user.id:raise HTTPException(403,"Нет доступа")
    if case.status not in {"DRAFT","REVISION_REQUIRED"}:
        raise HTTPException(409,"Карточка уже на согласовании. Изменения возможны после возврата на доработку.")
    f=await request.form()
    require_csrf(request,f)
    data={key:safe(f.get(key)) for key in FIELDS}
    required=("student_name","group_name","department","program","company_full_name","company_address",
              "company_inn","director_position","director_name","director_basis",
              "contact_name","contact_phone","room_address")
    for key in required:
        if not data[key]:raise HTTPException(422,"Не заполнено обязательное поле: "+key)
    if not data["company_inn"].isdigit() or len(data["company_inn"]) not in (10,12):
        raise HTTPException(422,"ИНН должен состоять из 10 или 12 цифр")
    start=safe(f.get("practice_start"),10)
    end=safe(f.get("practice_end"),10)
    try:
        if datetime.strptime(start,"%Y-%m-%d")>datetime.strptime(end,"%Y-%m-%d"):raise ValueError()
    except ValueError:raise HTTPException(422,"Укажите корректный период практики")
    practice=safe(f.get("practice_type"),70)
    if practice not in ("Производственная","Учебная","Преддипломная"):
        raise HTTPException(422,"Неверный вид практики")
    for key,value in data.items():setattr(card,key,value)
    card.practice_paid=f.get("practice_paid")=="yes"
    case.practice_type=practice
    case.practice_start=start
    case.practice_end=end
    case.company_name=data["company_full_name"][:210]
    case.title="Договор о практической подготовке — "+data["company_full_name"][:140]
    log_event(db,user,"company_card_edited","agreement",case.id,"Изменена существующая карточка без создания нового договора")
    db.commit()
    return RedirectResponse(f"/agreements/{case.id}/company-card",status_code=303)

def get_case(db,user,agreement_id):
    case=db.get(Agreement,agreement_id)
    if not case:raise HTTPException(404,"Договор не найден")
    if user.role=="STUDENT" and (case.student_id!=user.id or case.is_demo):raise HTTPException(403,"Нет доступа")
    if user.role=="ENTERPRISE" and (case.enterprise_user_id!=user.id or case.is_demo):raise HTTPException(403,"Нет доступа")
    card=db.scalar(select(CompanyCard).where(CompanyCard.agreement_id==case.id))
    if not card:raise HTTPException(404,"Карточка предприятия не заполнена")
    return case,card

@router.get("/agreements/{agreement_id}/company-card")
def show(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    return views.TemplateResponse(request,"company_card_view.html",context(request,user,case=case,card=card))

def build_docx(case,card):
    # The approved, unchanged legal text is supplied as a DOCX template.
    # Never substitute an abbreviated contract when the approved template
    # is unavailable: fail closed rather than issue an incorrect document.
    from .storage import get_file
    with SessionLocal() as template_db:
        latest=template_db.scalar(select(DocumentTemplate).where(DocumentTemplate.template_kind=="practice_agreement").order_by(DocumentTemplate.id.desc()).limit(1))
        if not latest:
            raise HTTPException(503,"Типовой договор образовательной организации ещё не установлен. Формирование временно недоступно.")
        template_bytes=get_file(latest.object_key)
    doc=Document(io.BytesIO(template_bytes))
    from datetime import date
    def fmt_date(value):
        try:return datetime.strptime(value,"%Y-%m-%d").strftime("%d.%m.%Y")
        except (ValueError,TypeError):return "________________"
    values={
        "contract_number":case.reference,
        "contract_date":"________________",
        "company_full_name":card.company_full_name,
        "company_short_name":card.company_short_name or "________________",
        "company_address":card.company_address,
        "company_address_short":card.company_address,
        "company_phone":card.company_phone or "________________",
        "company_inn":card.company_inn,
        "company_kpp":card.company_kpp or "________________",
        "company_ogrn":card.company_ogrn or "________________",
        "company_okpo":card.company_okpo or "________________",
        "company_okved":card.company_okved or "________________",
        "director_name":card.director_name,
        "director_name_genitive":"________________",
        "director_initials":"________________",
        "practice_start":fmt_date(case.practice_start),
        "practice_end":fmt_date(case.practice_end),
        "academic_year":str(datetime.utcnow().year)+"/"+str(datetime.utcnow().year+1),
        "department":card.department,
        "program":card.program,
        "course_name":card.course_name or "________________",
        "group_name":card.group_name,
        "student_name":card.student_name,
        "practice_type_and_payment":case.practice_type+(" (оплачиваемая)" if card.practice_paid else " (без оплаты)"),
        "university_contact":card.university_contact or "________________",
        "university_phone":card.university_phone or "________________",
        "university_email":card.university_email or "________________",
    }
    def replace_runs(paragraph):
        # Replacements preserve existing DOCX styles when placeholders are
        # contained in a run. Cross-run placeholders are reconstructed.
        for run in paragraph.runs:
            for key,value in values.items():
                run.text=run.text.replace("{{"+key+"}}",value)
        original="".join(r.text for r in paragraph.runs)
        if "{{" not in original:return
        updated=original
        for key,value in values.items():updated=updated.replace("{{"+key+"}}",value)
        if updated!=original and paragraph.runs:
            paragraph.runs[0].text=updated
            for run in paragraph.runs[1:]:run.text=""
    for paragraph in doc.paragraphs:replace_runs(paragraph)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:replace_runs(paragraph)
    buffer=io.BytesIO()
    doc.save(buffer);buffer.seek(0)
    return buffer


@router.get("/agreements/{agreement_id}/generate-docx")
def download_filename(case, card, ext):
    from urllib.parse import quote
    parts=(card.student_name or "").split()
    label=(parts[0]+" "+parts[1][0]+"."+parts[2][0]+".") if len(parts)>=3 else (" ".join(parts) or "Договор")
    return "attachment; filename=agreement_"+str(case.id)+"."+ext+"; filename*=UTF-8"+chr(39)*2+quote(label+"."+ext)

def download(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    out=build_docx(case,card)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       headers={"Content-Disposition":download_filename(case,card,"docx"),
                "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})


@router.get("/agreements/{agreement_id}/preview-pdf")
def preview_generated_pdf(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    """Same draft as downloadable PDF, embedded on the agreement page."""
    import subprocess
    import tempfile
    from pathlib import Path
    user=require_user(request,db)
    case,card=get_case(db,user,agreement_id)
    with tempfile.TemporaryDirectory(prefix="cifra_preview_") as folder:
        work=Path(folder)
        source=work/"agreement.docx"
        source.write_bytes(build_docx(case,card).getvalue())
        result=subprocess.run(
            ["libreoffice","-env:UserInstallation=file://"+str(work/"profile"),
             "--headless","--convert-to","pdf:writer_pdf_Export",
             "--outdir",str(work),str(source)],
            capture_output=True,text=True,timeout=75,check=False)
        output=work/"agreement.pdf"
        if result.returncode!=0 or not output.exists():
            raise HTTPException(503,"Предпросмотр временно недоступен")
        data=output.read_bytes()
    return StreamingResponse(io.BytesIO(data),media_type="application/pdf",
        headers={"Content-Disposition":"inline","Cache-Control":"private, no-store",
                 "Content-Security-Policy":"sandbox allow-same-origin"})

@router.get("/agreements/{agreement_id}/generate-pdf")
def download_pdf(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    """Render the exact same DOCX document to PDF using LibreOffice."""
    import subprocess
    import tempfile
    from pathlib import Path
    user=require_user(request,db)
    case,card=get_case(db,user,agreement_id)
    source=build_docx(case,card)
    with tempfile.TemporaryDirectory(prefix="cifra_pdf_") as folder:
        work=Path(folder)
        docx_path=work/"agreement.docx"
        docx_path.write_bytes(source.getvalue())
        result=subprocess.run(
            ["libreoffice","-env:UserInstallation=file://"+str(work/"lo-profile"),
             "--headless","--convert-to","pdf:writer_pdf_Export",
             "--outdir",str(work),str(docx_path)],
            capture_output=True,text=True,timeout=75,check=False)
        pdf_path=work/"agreement.pdf"
        if result.returncode!=0 or not pdf_path.exists():
            raise HTTPException(503,"Не удалось сформировать PDF. Попробуйте скачать Word.")
        content=pdf_path.read_bytes()
    return StreamingResponse(io.BytesIO(content),media_type="application/pdf",
        headers={"Content-Disposition":download_filename(case,card,"pdf"),
                 "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
