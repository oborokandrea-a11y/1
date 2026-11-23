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
from .database import get_db
from .models import User, Agreement, CompanyCard, AgreementNumberSequence
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
    template_path=ROOT/"templates"/"documents"/"practice_agreement.docx"
    if not template_path.exists():
        raise HTTPException(503,"Типовой договор НИТУ МИСИС ещё не установлен. Формирование временно недоступно.")
    doc=Document(str(template_path))
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

def build_docx_legacy(case,card):
    doc=Document()
    section=doc.sections[0]
    section.top_margin=Cm(1.8);section.bottom_margin=Cm(1.7)
    section.left_margin=Cm(2.4);section.right_margin=Cm(1.5)
    style=doc.styles["Normal"];style.font.name="Times New Roman";style.font.size=Pt(11)
    style.paragraph_format.space_after=Pt(5)
    style.paragraph_format.line_spacing=1.15
    for sec in doc.sections:
        sec.header_distance=Cm(0.8)
        sec.footer_distance=Cm(0.8)
    def p(text="",bold=False):
        para=doc.add_paragraph()
        run=para.add_run(text);run.bold=bold
        return para
    def heading(text):
        para=doc.add_paragraph();para.alignment=1;run=para.add_run(text);run.bold=True;run.font.size=Pt(11)
    def date_ru(s):
        return datetime.strptime(s,"%Y-%m-%d").strftime("%d.%m.%Y")
    heading("Проект договора № "+case.reference)
    heading("об организации и проведении практической подготовки обучающихся НИТУ МИСИС")
    p("Дата заключения определяется при подписании сторонами.")
    p("Федеральное государственное автономное образовательное учреждение высшего образования «Национальный исследовательский технологический университет «МИСИС», именуемое далее «Университет», в лице уполномоченного представителя, сведения о котором заполняются университетом перед подписанием, с одной стороны, и "+card.company_full_name+", именуемое далее «Профильная организация», в лице "+card.director_position+" "+card.director_name+", действующего на основании "+card.director_basis+", с другой стороны, заключили настоящий договор.")
    heading("1 Предмет договора")
    p("1.1. Предметом договора является взаимодействие сторон по организации и проведению практической подготовки обучающихся Университета в рамках образовательных программ.")
    p("1.2. Образовательная программа, вид практики, сроки и сведения об обучающемся определены в приложении № 1.")
    p("1.3. Помещения профильной организации, используемые для практической подготовки, указаны в приложении № 2.")
    heading("2 Права и обязанности сторон")
    p("2.1. Университет назначает руководителя практики, организует направление обучающегося, обеспечивает методическое сопровождение и контролирует выполнение программы практики.")
    p("2.2. Профильная организация предоставляет условия и оборудование для практики, назначает ответственное лицо, обеспечивает охрану труда, инструктаж и соблюдение требований безопасности.")
    p("2.3. Обучающийся обязан соблюдать правила внутреннего трудового распорядка, охраны труда и режим конфиденциальности.")
    heading("3 Срок действия договора")
    p("3.1. Договор вступает в силу со дня подписания и действует до "+date_ru(case.practice_end)+".")
    p("3.2. Изменения и прекращение договора оформляются по соглашению сторон в установленном порядке.")
    heading("4 Ответственность сторон")
    p("4.1. Стороны несут ответственность в соответствии с законодательством Российской Федерации.")
    heading("5 Заключительные положения")
    p("5.1. Изменения и дополнения оформляются письменно и подписываются уполномоченными представителями.")
    p("5.2. Договор составляется в двух экземплярах, по одному для каждой стороны.")
    p("5.3. Контактное лицо профильной организации: "+card.contact_name+", тел. "+card.contact_phone+", e-mail "+card.contact_email+".")
    p("5.4. Контактное лицо университета: "+(card.university_contact or "уточняется университетом")+", тел. "+(card.university_phone or "не указан")+", e-mail "+(card.university_email or "не указан")+".")
    heading("6 Адреса и реквизиты сторон")
    p("Университет: НИТУ МИСИС, 119049, Москва, Ленинский проспект, д. 4, стр. 1. ИНН 7706019535, КПП 770601001.")
    p("Профильная организация: "+card.company_full_name+" ("+card.company_short_name+"), "+card.company_address+", ИНН "+card.company_inn+", КПП "+card.company_kpp+", ОГРН "+card.company_ogrn+", ОКПО "+card.company_okpo+", ОКВЭД "+card.company_okved+".")
    p("Фактический адрес: "+card.company_actual_address+"; телефон: "+card.company_phone+"; факс: "+card.company_fax+".")
    p("Банковские реквизиты: "+card.bank_name+", расчётный счёт "+card.bank_account+", корреспондентский счёт "+card.correspondent_account+", БИК "+card.bank_bik+".")
    p("От Университета: уполномоченный представитель НИТУ МИСИС     От Профильной организации: "+card.director_name)
    p("Подписи сторон проставляются при подписании договора. Печать — при наличии и необходимости.")
    doc.add_page_break()
    heading("Приложение № 1")
    heading("СОВМЕСТНЫЙ РАБОЧИЙ ПЛАН")
    p("проведения практической подготовки")
    t=doc.add_table(rows=0,cols=2);t.style="Table Grid"
    for key,val in [
        ("Вид практики",case.practice_type+(", оплачивается" if card.practice_paid else ", не оплачивается")),
        ("Кафедра",card.department),("Курс, группа",card.course_name+", "+card.group_name),
        ("Образовательная программа",card.program),("Начало практики",date_ru(case.practice_start)),
        ("Окончание практики",date_ru(case.practice_end)),("Ф.И.О. обучающегося",card.student_name)]:
        row=t.add_row().cells;row[0].text=key;row[1].text=val
    p("От Университета: уполномоченный представитель НИТУ МИСИС       От Профильной организации: "+card.director_name)
    p("Подпись: ______________ М.П.                     Подпись: ______________ М.П.")
    doc.add_page_break()
    heading("Приложение № 2")
    heading("Помещения профильной организации для проведения практической подготовки")
    t=doc.add_table(rows=1,cols=3);t.style="Table Grid"
    for c,v in zip(t.rows[0].cells,("№ п/п","Наименование подразделения","Адрес, кабинет / помещение")):c.text=v
    row=t.add_row().cells;row[0].text="1";row[1].text=card.room_department or "Уточняется";row[2].text=card.room_address
    p("От Университета: уполномоченный представитель НИТУ МИСИС       От Профильной организации: "+card.director_name)
    p("Подпись: ______________ М.П.                     Подпись: ______________ М.П.")
    doc.core_properties.title="Проект договора о практической подготовке — НИТУ МИСИС"
    footer=doc.sections[0].footer.paragraphs[0]
    footer.alignment=1
    footer.add_run("Проект договора № "+case.reference+" · Не подписан сторонами").font.size=Pt(8)
    out=io.BytesIO();doc.save(out);out.seek(0);return out

@router.get("/agreements/{agreement_id}/generate-docx")
def download(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    out=build_docx(case,card)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       headers={"Content-Disposition":f'attachment; filename="cifra_agreement_{case.id}_draft.docx"',
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
        headers={"Content-Disposition":f'attachment; filename="cifra_agreement_{case.id}_draft.pdf"',
                 "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
