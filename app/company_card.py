"""Company questionnaire and downloadable editable draft agreement (not a signature)."""
import io
import re
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
    return re.sub(r"[\\x00-\\x1f]+", " ", str(v or "")).strip()[:limit]

def context(request, user, **kw):
    return dict(user=user,active="agreements",role_labels=ROLE_LABELS,status_labels=STATUS_LABELS,
        csrf_token=csrf_token(request),flash=None,**kw)

@router.get("/company-card/new")
def form(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role!="STUDENT":raise HTTPException(403,"Анкета доступна студенту")
    existing=db.scalar(select(Agreement).join(CompanyCard,CompanyCard.agreement_id==Agreement.id).where(Agreement.student_id==user.id).order_by(Agreement.id.desc()))
    if existing:return RedirectResponse(f"/agreements/{existing.id}/company-card",status_code=303)
    return views.TemplateResponse(request,"company_card.html",context(request,user,card=None))

@router.post("/company-card/new")
async def submit(request:Request,db:Session=Depends(get_db)):
    user=require_user(request,db)
    if user.role!="STUDENT":raise HTTPException(403,"Анкета доступна студенту")
    existing=db.scalar(select(Agreement.id).join(CompanyCard,CompanyCard.agreement_id==Agreement.id).where(Agreement.student_id==user.id))
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
    case=Agreement(reference="PENDING",
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

def get_case(db,user,agreement_id):
    case=db.get(Agreement,agreement_id)
    if not case:raise HTTPException(404,"Договор не найден")
    if user.role=="STUDENT" and case.student_id!=user.id:raise HTTPException(403,"Нет доступа")
    if user.role=="ENTERPRISE" and case.enterprise_user_id!=user.id:raise HTTPException(403,"Нет доступа")
    card=db.scalar(select(CompanyCard).where(CompanyCard.agreement_id==case.id))
    if not card:raise HTTPException(404,"Карточка предприятия не заполнена")
    return case,card

@router.get("/agreements/{agreement_id}/company-card")
def show(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    return views.TemplateResponse(request,"company_card_view.html",context(request,user,case=case,card=card))

def build_docx(case,card):
    doc=Document()
    section=doc.sections[0]
    section.top_margin=Cm(1.8);section.bottom_margin=Cm(1.7)
    section.left_margin=Cm(2.4);section.right_margin=Cm(1.5)
    style=doc.styles["Normal"];style.font.name="Times New Roman";style.font.size=Pt(11)
    style.paragraph_format.space_after=Pt(5)
    def p(text="",bold=False):
        para=doc.add_paragraph()
        run=para.add_run(text);run.bold=bold
        return para
    def heading(text):
        para=doc.add_paragraph();para.alignment=1;run=para.add_run(text);run.bold=True
    def date_ru(s):
        return datetime.strptime(s,"%Y-%m-%d").strftime("%d.%m.%Y")
    heading("Проект договора № "+case.reference)
    heading("об организации и проведении практической подготовки обучающихся НИТУ МИСИС")
    p("Дата заключения: «___» __________ 20___ г.")
    p("Федеральное государственное автономное образовательное учреждение высшего образования «Национальный исследовательский технологический университет «МИСИС», именуемое далее «Университет», в лице уполномоченного представителя ____________________, действующего на основании ____________________, с одной стороны, и "+card.company_full_name+", именуемое далее «Профильная организация», в лице "+card.director_position+" "+card.director_name+", действующего на основании "+card.director_basis+", с другой стороны, заключили настоящий договор.")
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
    p("5.4. Контактное лицо университета: "+(card.university_contact or "________________")+", тел. "+(card.university_phone or "________________")+", e-mail "+(card.university_email or "________________")+".")
    heading("6 Адреса и реквизиты сторон")
    p("Университет: НИТУ МИСИС, 119049, Москва, Ленинский проспект, д. 4, стр. 1. ИНН 7706019535, КПП 770601001.")
    p("Профильная организация: "+card.company_full_name+" ("+card.company_short_name+"), "+card.company_address+", ИНН "+card.company_inn+", КПП "+card.company_kpp+", ОГРН "+card.company_ogrn+", ОКПО "+card.company_okpo+", ОКВЭД "+card.company_okved+".")
    p("Фактический адрес: "+card.company_actual_address+"; телефон: "+card.company_phone+"; факс: "+card.company_fax+".")
    p("Банковские реквизиты: "+card.bank_name+", расчётный счёт "+card.bank_account+", корреспондентский счёт "+card.correspondent_account+", БИК "+card.bank_bik+".")
    p("От Университета: ____________________     От Профильной организации: "+card.director_name)
    p("Подпись: ______________ М.П.                     Подпись: ______________ М.П.")
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
    p("От Университета: ____________________       От Профильной организации: "+card.director_name)
    p("Подпись: ______________ М.П.                     Подпись: ______________ М.П.")
    doc.add_page_break()
    heading("Приложение № 2")
    heading("Помещения профильной организации для проведения практической подготовки")
    t=doc.add_table(rows=1,cols=3);t.style="Table Grid"
    for c,v in zip(t.rows[0].cells,("№ п/п","Наименование подразделения","Адрес, кабинет / помещение")):c.text=v
    row=t.add_row().cells;row[0].text="1";row[1].text=card.room_department or "Уточняется";row[2].text=card.room_address
    p("От Университета: ____________________       От Профильной организации: "+card.director_name)
    p("Подпись: ______________ М.П.                     Подпись: ______________ М.П.")
    doc.core_properties.title="Черновик договора о практической подготовке — НИТУ МИСИС"
    out=io.BytesIO();doc.save(out);out.seek(0);return out

@router.get("/agreements/{agreement_id}/generate-docx")
def download(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    out=build_docx(case,card)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       headers={"Content-Disposition":f'attachment; filename="cifra_agreement_{case.id}_draft.docx"',
                "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})


@router.get("/agreements/{agreement_id}/generate-pdf")
def download_pdf(request:Request,agreement_id:int,db:Session=Depends(get_db)):
    """Private unsigned PDF preview of the agreement and both annexes."""
    from xml.sax.saxutils import escape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    user=require_user(request,db);case,card=get_case(db,user,agreement_id)
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    pdfmetrics.registerFont(TTFont("CifraSans",font))
    normal=ParagraphStyle("body",fontName="CifraSans",fontSize=9,leading=14,spaceAfter=9)
    heading=ParagraphStyle("head",parent=normal,fontSize=11,leading=16,alignment=TA_CENTER,spaceAfter=14)
    out=io.BytesIO();pdf=SimpleDocTemplate(out,pagesize=A4,leftMargin=48,rightMargin=48,topMargin=45,bottomMargin=45)
    def para(t,style=normal):return Paragraph(escape(str(t or "")),style)
    def dr(s):return datetime.strptime(s,"%Y-%m-%d").strftime("%d.%m.%Y")
    story=[para("ПРОЕКТ ДОГОВОРА № "+case.reference,heading),para("Об организации и проведении практической подготовки обучающихся НИТУ МИСИС",heading),
      para("Документ сформирован автоматически. Не подписан. Требует проверки университетом."),
      para("ФГАОУ ВО «НИТУ МИСИС» (Университет) и "+card.company_full_name+" (Профильная организация) в лице "+card.director_position+" "+card.director_name+", действующего на основании "+card.director_basis+", заключают договор о практической подготовке."),
      para("1. Предмет договора",heading),para("Стороны организуют практическую подготовку обучающегося в соответствии с совместным рабочим планом (приложение № 1) и перечнем помещений (приложение № 2)."),
      para("2. Права и обязанности сторон",heading),para("Университет организует и контролирует практику. Профильная организация предоставляет условия для практической подготовки, обеспечивает инструктаж и соблюдение требований безопасности."),
      para("3. Срок действия договора",heading),para("Договор действует со дня подписания до "+dr(case.practice_end)+"."),
      para("4. Ответственность сторон",heading),para("Стороны несут ответственность в соответствии с законодательством Российской Федерации."),
      para("5. Заключительные положения",heading),para("Изменения оформляются письменно и подписываются уполномоченными представителями."),
      para("6. Реквизиты и подписи",heading),para("Университет: НИТУ МИСИС, Москва, Ленинский проспект, д. 4, стр. 1."),
      para("Предприятие: "+card.company_full_name+"; адрес: "+card.company_address+"; ИНН: "+card.company_inn+"; КПП: "+card.company_kpp+"; ОГРН: "+card.company_ogrn+"."),
      para("Банк: "+card.bank_name+"; расчётный счёт: "+card.bank_account+"; корр. счёт: "+card.correspondent_account+"; БИК: "+card.bank_bik+"."),
      para("От университета: ____________________    От предприятия: ____________________"),PageBreak(),
      para("Приложение № 1. Совместный рабочий план",heading)]
    rows=[("Вид практики",case.practice_type),("Кафедра",card.department),("Курс и группа",card.course_name+" "+card.group_name),("Программа",card.program),("Сроки",dr(case.practice_start)+" — "+dr(case.practice_end)),("Студент",card.student_name)]
    t=Table([[para(k),para(v)] for k,v in rows],colWidths=[155,340],hAlign="LEFT");t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]));story.extend([t,PageBreak(),para("Приложение № 2. Помещения для практики",heading),para("Подразделение: "+card.room_department),para("Адрес: "+card.room_address)])
    pdf.build(story);out.seek(0)
    return StreamingResponse(out,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="cifra_agreement_{case.id}_unsigned.pdf"',"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
