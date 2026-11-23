"""Company questionnaire and downloadable editable draft agreement (not a signature)."""
import io
import re
import secrets
from datetime import datetime, timedelta
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

def validate_card_fields(data):
    required=[field for field in FIELDS if field!="registration_country"]
    if data.get("residency")=="Нерезидент":
        required.append("registration_country")
    for field in required:
        if not data.get(field):
            raise HTTPException(422,"Не заполнено обязательное поле: "+field)
    for field in ("contact_email","university_email"):
        value=data.get(field,"")
        if "@" not in value or "." not in value.rsplit("@",1)[-1]:
            raise HTTPException(422,"Некорректная электронная почта: "+field)


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
    validate_card_fields(data)
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
    validate_card_fields(data)
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

def add_demo_seals_at_mp(doc):
    """Insert clearly fictional signing marks inside existing М.П. paragraphs."""
    from PIL import Image, ImageDraw, ImageFont
    from docx.shared import Cm
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    image=Image.new("RGBA",(420,420),(255,255,255,0))
    draw=ImageDraw.Draw(image)
    blue=(27,87,173,235)
    for inset,width in ((8,7),(24,3),(54,3)):
        draw.ellipse((inset,inset,420-inset,420-inset),outline=blue,width=width)
    try:
        font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        big=ImageFont.truetype(font,39)
        small=ImageFont.truetype(font,22)
    except OSError:
        big=small=ImageFont.load_default()
    for y,label,face in ((156,"ОБРАЗЕЦ",big),(208,"ЭЛЕКТРОННО",small),
                          (242,"ПОДПИСАНО",small),(282,"НЕ УКЭП",small)):
        draw.text((210,y),label,fill=blue,font=face,anchor="mm")
    buffer=io.BytesIO()
    image.save(buffer,format="PNG")
    count=0
    def put(paragraph):
        nonlocal count
        if not re.search(r"М\s*\.\s*П\s*\.",paragraph.text,re.I):
            return
        if count>=12:return
        # Floating mark overlays the existing М.П. letters, with no new line
        # or table-row height. One mark per М.П. paragraph.
        runs=list(paragraph.runs)
        full="".join(run.text for run in runs)
        match=re.search(r"М\\s*\\.\\s*П\\s*\\.",full,re.I)
        if not match:return
        offset=0
        target=None
        for run in runs:
            offset+=len(run.text)
            if offset>=match.end():
                target=run
                break
        if target is None:return
        buffer.seek(0)
        pic=paragraph.add_run()
        pic.add_picture(buffer,width=Cm(1.15))
        target._r.addnext(pic._r)
        inline=pic._r.xpath(".//wp:inline")
        if not inline:return
        from copy import deepcopy
        original=inline[0]
        anchor=OxmlElement("wp:anchor")
        for key,value in {
            "distT":"0","distB":"0","distL":"0","distR":"0",
            "simplePos":"0","relativeHeight":"251659264",
            "behindDoc":"0","locked":"0","layoutInCell":"1","allowOverlap":"1"
        }.items():anchor.set(key,value)
        simple=OxmlElement("wp:simplePos")
        simple.set("x","0");simple.set("y","0")
        anchor.append(simple)
        horizontal=OxmlElement("wp:positionH")
        horizontal.set("relativeFrom","character")
        hpos=OxmlElement("wp:posOffset")
        hpos.text="-800000"
        horizontal.append(hpos);anchor.append(horizontal)
        vertical=OxmlElement("wp:positionV")
        vertical.set("relativeFrom","line")
        vpos=OxmlElement("wp:posOffset")
        vpos.text="-500000"
        vertical.append(vpos);anchor.append(vertical)
        wrap=OxmlElement("wp:wrapNone");anchor.append(wrap)
        for child in list(original):
            anchor.append(deepcopy(child))
        original.getparent().replace(original,anchor)
        count+=1
    def scan_table(table):
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:put(paragraph)
                for nested in cell.tables:scan_table(nested)
    for paragraph in doc.paragraphs:put(paragraph)
    for table in doc.tables:scan_table(table)
    return count


def build_demo_docx(case,card):
    """Clearly marked test-only document, never an official agreement."""
    doc=Document()
    section=doc.sections[0]
    section.header.paragraphs[0].text="ЦИФРА ЭДО · ТЕСТОВЫЙ РЕЖИМ"
    doc.add_heading(f"Договор № {case.id} (демонстрационный)",0)
    doc.add_paragraph("ТЕСТОВЫЙ ДОКУМЕНТ. НЕ ЯВЛЯЕТСЯ ДОГОВОРОМ И НЕ ПОДЛЕЖИТ ПОДПИСАНИЮ.")
    for label,value in [
        ("Студент",card.student_name),
        ("Группа",card.group_name),
        ("Организация",card.company_full_name),
        ("Адрес",card.company_address),
        ("Вид практики",case.practice_type),
        ("Начало практики",case.practice_start),
        ("Окончание практики",case.practice_end),
        ("Текущий этап (симуляция)",case.status),
    ]:
        doc.add_paragraph(f"{label}: {value or 'Не указано'}")
    doc.add_paragraph("Реальные согласования и электронные подписи отсутствуют.")
    add_demo_seals_at_mp(doc)
    buffer=io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def build_docx(case,card):
    # The approved, unchanged legal text is supplied as a DOCX template.
    # Never substitute an abbreviated contract when the approved template
    # is unavailable: fail closed rather than issue an incorrect document.
    from .storage import get_file
    with SessionLocal() as template_db:
        latest=template_db.scalar(select(DocumentTemplate).where(DocumentTemplate.template_kind=="practice_agreement").order_by(DocumentTemplate.id.desc()).limit(1))
        if not latest:
            if case.is_demo:
                return build_demo_docx(case,card)
            raise HTTPException(503,"Типовой договор образовательной организации ещё не установлен. Формирование временно недоступно.")
        template_bytes=get_file(latest.object_key)
    doc=Document(io.BytesIO(template_bytes))
    from datetime import date
    def fmt_date(value):
        try:return datetime.strptime(value,"%Y-%m-%d").strftime("%d.%m.%Y")
        except (ValueError,TypeError):return "________________"
    def director_initials(full_name):
        parts=str(full_name or "").split()
        if len(parts)<2 or full_name=="Не указано":return "________________"
        return parts[0]+" "+"".join(part[0]+"." for part in parts[1:])
    def director_genitive(full_name):
        # Inflection cannot be reliably inferred for all Russian names.
        # Keep the original spelling rather than silently invent a legal signatory.
        return full_name if full_name and full_name!="Не указано" else "________________"
    demo_completed=bool(case.is_demo and case.status=="COMPLETED")
    demo_director=("Смирнов Алексей Викторович" if (card.student_name or "").startswith("Орлов") else "Беляев Дмитрий Павлович") if demo_completed else None
    director_for_document=demo_director or card.director_name
    values={
        "contract_number":(str(case.id) if case.is_demo else case.reference),
        "contract_date":((datetime(2026,1,1)+timedelta(days=int(case.id or 0))).strftime("%d.%m.%Y") if case.is_demo else (case.created_at.strftime("%d.%m.%Y") if case.created_at else datetime.utcnow().strftime("%d.%m.%Y"))),
        "company_full_name":card.company_full_name,
        "company_short_name":card.company_short_name or "________________",
        "company_address":card.company_address,
        "company_address_short":card.company_address,
        "company_phone":card.company_phone or ("+7 (000) 000-00-00" if case.is_demo else "________________"),
        "company_inn":card.company_inn or ("УЧЕБНЫЙ" if case.is_demo else "________________"),
        "company_kpp":card.company_kpp or ("УЧЕБНЫЙ" if case.is_demo else "________________"),
        "company_ogrn":card.company_ogrn or ("УЧЕБНЫЙ" if case.is_demo else "________________"),
        "company_okpo":card.company_okpo or ("УЧЕБНЫЙ" if case.is_demo else "________________"),
        "company_okved":card.company_okved or ("УЧЕБНЫЙ" if case.is_demo else "________________"),
        "room_department":card.room_department or ("Учебный отдел практической подготовки" if case.is_demo else "________________"),
        "room_address":card.room_address or ("г. Москва, Учебная улица, д. 2, аудитория 101 (условный адрес)" if case.is_demo else "________________"),
        "director_name":director_for_document if director_for_document and director_for_document!="Не указано" else "________________",
        "director_name_genitive":director_genitive(director_for_document),
        "director_initials":director_initials(director_for_document),
        "practice_start":fmt_date(case.practice_start),
        "practice_end":fmt_date(case.practice_end),
        "academic_year":str(datetime.utcnow().year)+"/"+str(datetime.utcnow().year+1),
        "department":card.department,
        "program":card.program,
        "course_name":card.course_name or "________________",
        "group_name":card.group_name,
        "student_name":card.student_name,
        "practice_type_and_payment":case.practice_type+(" (оплачиваемая)" if card.practice_paid else " (без оплаты)"),
        "university_contact":card.university_contact or ("Морозова Анна Викторовна" if case.is_demo else "________________"),
        "university_phone":card.university_phone or ("+7 (000) 123-45-67" if case.is_demo else "________________"),
        "university_email":card.university_email or ("practice@example.org" if case.is_demo else "________________"),
        "university_position":("доцент, к.н." if case.is_demo else "________________"),
    }
    def replace_runs(paragraph):
        # Replace across run boundaries without flattening all formatting into
        # the first run. The source document's typography remains intact.
        runs=list(paragraph.runs)
        if not runs:return
        for key,value in values.items():
            token="{{"+key+"}}"
            while True:
                full="".join(run.text for run in runs)
                pos=full.find(token)
                if pos<0:break
                end=pos+len(token)
                offset=0
                first=last=None
                first_pos=last_pos=0
                for idx,run in enumerate(runs):
                    next_offset=offset+len(run.text)
                    if first is None and pos<next_offset:
                        first=idx;first_pos=pos-offset
                    if first is not None and end<=next_offset:
                        last=idx;last_pos=end-offset;break
                    offset=next_offset
                if first is None or last is None:break
                if first==last:
                    t=runs[first].text
                    runs[first].text=t[:first_pos]+str(value)+t[last_pos:]
                else:
                    before=runs[first].text[:first_pos]
                    after=runs[last].text[last_pos:]
                    runs[first].text=before+str(value)
                    for idx in range(first+1,last):runs[idx].text=""
                    runs[last].text=after
    for paragraph in doc.paragraphs:replace_runs(paragraph)
    if case.is_demo:
        # Complete the university practice-contact clause in the source template.
        contact=("Учебное подразделение, доцент, к.н. (должность) "
                 "Морозова Анна Викторовна (ф.и.о.), "
                 "+7 (000) 123-45-67 (телефон), practice@example.org (e-mail).")
        for paragraph in doc.paragraphs:
            if "Контактная информация ответственного лица" in paragraph.text:
                # Replace only this contact paragraph; leave section 5.6 intact.
                for run in paragraph.runs:run.text=""
                paragraph.add_run("Контактная информация ответственного лица за заключение договора "
                                  "и организацию практической подготовки со стороны Университета: "+contact)

    # Some uploaded templates incorrectly put the student's initials in the
    # agreement-number field instead of using {{contract_number}}.
    # Correct only demo documents, retaining the approved real template.
    if case.is_demo:
        for paragraph in doc.paragraphs:
            if re.search(r"Договор\\s*№\\s*[А-ЯЁ][а-яё]+\\s+[А-ЯЁ]\\.[А-ЯЁ]\\.",paragraph.text,re.I):
                for run in paragraph.runs:
                    run.text=re.sub(r"(Договор\\s*№\\s*)[А-ЯЁ][а-яё]+\\s+[А-ЯЁ]\\.[А-ЯЁ]\\.",r"\\g<1>"+str(case.id),run.text,flags=re.I)

    # XML cell wrappers can be recreated for each access; id(cell._tc)
    # may be reused and incorrectly skip other cells. Deduplicate by
    # the underlying XML element, not its temporary Python object id.
    visited=set()
    def process_table(table):
        for row in table.rows:
            for cell in row.cells:
                if cell._tc in visited:continue
                visited.add(cell._tc)
                for paragraph in cell.paragraphs:replace_runs(paragraph)
                for nested in cell.tables:process_table(nested)
    for table in doc.tables:process_table(table)
    if case.is_demo:
        # Populate the existing rooms table, without appending any new pages.
        for table in doc.tables:
            for row in table.rows:
                cells=row.cells
                if len(cells)<3:continue
                first=cells[0].text.strip()
                second=cells[1].text.strip()
                third=cells[2].text.strip()
                if first in ("1", "1.", "1. ") and ("г. Москва" in third or not second or not third):
                    room_name=card.room_department or "Отдел аналитики и практической подготовки"
                    room_address=card.room_address or "г. Москва, ул. Учебная, д. 12, этаж 3, кабинет 305 (условный адрес)"
                    cells[1].text=room_name
                    cells[2].text=room_address
    for section in doc.sections:
        for part in (section.header,section.footer):
            for paragraph in part.paragraphs:replace_runs(paragraph)
            for table in part.tables:process_table(table)
    unresolved=[]
    for paragraph in doc.paragraphs:
        unresolved.extend(re.findall(r"\\{\\{[^{}]+\\}\\}",paragraph.text))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    unresolved.extend(re.findall(r"\\{\\{[^{}]+\\}\\}",paragraph.text))
    if unresolved:
        raise HTTPException(422,"В шаблоне остались незаполненные поля: "+", ".join(sorted(set(unresolved))))
    # Demo stamps belong at the existing М.П. fields, never in an appended block.
    # PDF gets position-accurate overlays after conversion, not DOCX anchors.
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


def overlay_demo_marks_pdf(pdf_bytes,case):
    """Overlay transparent training marks on existing PDF text, no reflow."""
    if not (case.is_demo and case.status=="COMPLETED"):
        return pdf_bytes
    import fitz
    from PIL import Image, ImageDraw, ImageFont
    image=Image.new("RGBA",(400,400),(255,255,255,0))
    draw=ImageDraw.Draw(image)
    ink=(20,78,161,245)
    draw.ellipse((8,8,392,392),outline=ink,width=9)
    draw.ellipse((28,28,372,372),outline=ink,width=4)
    try:
        font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        large=ImageFont.truetype(font,35)
        small=ImageFont.truetype(font,21)
    except OSError:
        large=small=ImageFont.load_default()
    for y,label,face in ((155,"ОБРАЗЕЦ",large),(207,"ЭЛЕКТРОННАЯ",small),
                          (245,"ПОДПИСЬ",small),(284,"НЕ УКЭП",small)):
        draw.text((200,y),label,fill=ink,font=face,anchor="mm")
    # Separate rectangular signing-status label, as in the supplied example.
    label_image=Image.new("RGBA",(900,220),(255,255,255,245))
    ld=ImageDraw.Draw(label_image)
    ld.rounded_rectangle((5,5,895,215),radius=22,outline=ink,width=7)
    try:
        label_font=ImageFont.truetype(font,32)
        foot_font=ImageFont.truetype(font,27)
    except (OSError,UnboundLocalError):
        label_font=foot_font=ImageFont.load_default()
    for y,line,face in ((49,"ДОКУМЕНТ ПОДПИСАН",label_font),
                         (91,"ЭЛЕКТРОННОЙ ПОДПИСЬЮ",label_font),
                         (133,"(ИМИТАЦИЯ)",label_font),
                         (181,"ОБРАЗЕЦ — НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ",foot_font)):
        ld.text((450,y),line,font=face,fill=ink,anchor="mm")
    label_bytes=io.BytesIO()
    label_image.save(label_bytes,format="PNG")
    stamp=io.BytesIO()
    image.save(stamp,format="PNG")
    pdf=fitz.open(stream=pdf_bytes,filetype="pdf")
    placed=0
    import logging
    logger=logging.getLogger("cifra.demo_seals")
    diagnostics=[]
    for page_index,page in enumerate(pdf):
        page_number=page_index+1
        # The approved six-page template has signing fields on pages 4-6.
        # Never place a demo stamp on page 2 or on narrative paragraphs.
        if page_number not in (4,5,6):
            diagnostics.append(f"{page_number}:skipped")
            continue
        positions=[]
        for term in ("М.П.", "М. П.", "М П", "Место печати"):
            positions.extend(page.search_for(term))
        # In appendices the signature fields sometimes have no literal М.П.
        # Only inspect the bottom signing area, not the entire page.
        if not positions:
            for term in ("(подпись)", "подпись"):
                positions.extend(hit for hit in page.search_for(term)
                                 if hit.y0 > page.rect.height*0.62)
        # When no text is searchable, use the existing lower signature area
        # only for the two appendices, with one mark per side.
        if not positions and page_number in (4,5,6):
            y=page.rect.height*0.83
            positions=[fitz.Rect(page.rect.width*0.24,y,page.rect.width*0.24+1,y+1),
                       fitz.Rect(page.rect.width*0.73,y,page.rect.width*0.73+1,y+1)]
        seen=set()
        # At most one mark in each left/right signature block per page.
        used_sides=set()
        for hit in sorted(positions,key=lambda h:(h.y0,h.x0),reverse=True):
            side="left" if (hit.x0+hit.x1)/2<page.rect.width/2 else "right"
            if side in used_sides:continue
            used_sides.add(side)
            cx=(hit.x0+hit.x1)/2
            cy=(hit.y0+hit.y1)/2
            size=58
            area=fitz.Rect(cx-size/2,cy-size/2,cx+size/2,cy+size/2)
            area=area & page.rect
            if area.is_empty:continue
            page.insert_image(area,stream=stamp.getvalue(),overlay=True,keep_proportion=True)
            # Keep the existing signer's printed ФИО visible below the
            # signature line. The label belongs ABOVE the seal, not beside
            # the name, and must never cover the bottom signatory text.
            column_left=0 if side=="left" else page.rect.width/2
            column_right=page.rect.width/2 if side=="left" else page.rect.width
            label_w=min(134,(column_right-column_left)-16)
            label_h=label_w*220/900
            label_x=max(column_left+8,min(cx-label_w/2,column_right-label_w-8))
            label_y=area.y0-label_h-9
            if label_y<8:
                # No safe room for a rectangular label: keep the original
                # signatory name unobstructed rather than overlapping it.
                label_y=8
            label_rect=fitz.Rect(label_x,label_y,label_x+label_w,label_y+label_h)
            page.insert_image(label_rect,stream=label_bytes.getvalue(),
                              overlay=True,keep_proportion=True)
            placed+=1
            seen.add(side)
        diagnostics.append(f"{page_number}:{len(seen)}")
    logger.warning("Demo PDF agreement=%s status=%s pages=%s stamp_count=%s per_page=%s",
                   case.id,case.status,len(pdf),placed,",".join(diagnostics))
    if placed==0:
        pdf.close()
        raise HTTPException(422,"Не найдены места М.П. или подписи в PDF. Проверьте исходный шаблон договора.")
    result=pdf.tobytes(garbage=3,deflate=True)
    pdf.close()
    return result


def trim_demo_pdf(pdf_bytes, case):
    """The demo PDF contains only the six requested template pages."""
    if not case.is_demo:
        return pdf_bytes
    from pypdf import PdfReader, PdfWriter
    reader=PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages)<=6:
        return pdf_bytes
    writer=PdfWriter()
    for page in reader.pages[:6]:
        writer.add_page(page)
    output=io.BytesIO()
    writer.write(output)
    return output.getvalue()


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
        data=overlay_demo_marks_pdf(trim_demo_pdf(output.read_bytes(),case),case)
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
        content=overlay_demo_marks_pdf(trim_demo_pdf(pdf_path.read_bytes(),case),case)
    return StreamingResponse(io.BytesIO(content),media_type="application/pdf",
        headers={"Content-Disposition":download_filename(case,card,"pdf"),
                 "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
