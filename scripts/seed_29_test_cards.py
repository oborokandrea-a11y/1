"""Create exactly 29 clearly marked synthetic company cards for workflow testing.
Idempotent, never generates signatures or impersonates approvals.
"""
import os
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User, Agreement, CompanyCard, AuditEvent
from app.security import hash_password
SURNAMES=["Орлов","Соколов","Миронов","Волков","Белов","Лебедев","Крылов","Фролов","Козлов","Зайцев","Морозов","Егоров","Громов","Тихонов","Баранов","Павлов","Борисов","Никитин","Макаров","Семёнов","Романов","Фёдоров","Алексеев","Ильин","Кузнецов","Данилов","Богданов","Савельев","Васильев"]
NAMES=["Артём","Илья","Максим","Даниил","Кирилл","Никита","Александр","Егор","Михаил","Павел"]
PATRONYMICS=["Андреевич","Сергеевич","Игоревич","Павлович","Олегович","Викторович"]
COMPANIES = [
  [
    "АО \"Гринатом\"",
    "г. Москва"
  ],
  [
    "ООО \"ТОРГОВЫЙ ДОМ \"ТУЛА-СТАЛЬ\"",
    "г. Москва"
  ],
  [
    "ООО «Технологии безопасности»",
    "г. Москва"
  ],
  [
    "АО \"Выксунский металлургический завод\"",
    "г. Выкса"
  ],
  [
    "ООО «ЗН Восток»",
    "Москва"
  ],
  [
    "Министерство финансов Российской Федерации",
    "Москва"
  ],
  [
    "АО \"Кэпт\"",
    "Москва"
  ],
  [
    "ООО «КДВ Краснодар»",
    "Краснодарский край, с. Белая Глина"
  ],
  [
    "АО «Деловые Решения и Технологии»",
    "Москва"
  ],
  [
    "ОБУЗ «Стоматологическая поликлиника №1»",
    "Иваново"
  ],
  [
    "Постоянное представительство Республики Карелия при Президенте РФ",
    "Москва"
  ],
  [
    "ООО «МАХТ»",
    "Москва"
  ],
  [
    "ООО \"Бусиново\"",
    "Москва"
  ],
  [
    "ООО «НеоНет»",
    "Москва"
  ],
  [
    "ООО «ПАР ГРУПП»",
    "Москва"
  ],
  [
    "ООО «Оружейные Мастерские»",
    "Владимирская область, Камешково"
  ],
  [
    "ООО \"Аудиторское бюро\"",
    "Москва"
  ],
  [
    "ООО «Корсо Милано»",
    "Москва"
  ],
  [
    "ГБУ \"АВТОМОБИЛЬНЫЕ ДОРОГИ ЦАО\"",
    "Москва"
  ],
  [
    "Ивановский государственный университет",
    "Иваново"
  ],
  [
    "ООО «СибЛайнер»",
    "Домодедово"
  ],
  [
    "ООО \"Б1 - Аудит\"",
    "Москва"
  ],
  [
    "ООО \"Тандем\"",
    "Москва"
  ],
  [
    "ООО «РЕСТОРАННА»",
    "Ульяновск"
  ],
  [
    "ООО \"Атлант-Стройресурс40\"",
    "Верея"
  ],
  [
    "ООО \"ПЕРСПЕКТИВА\"",
    "Москва"
  ],
  [
    "ООО «ФАСАД СТРОЙ»",
    "Москва"
  ],
  [
    "АО \"СО ЕЭС\"",
    "Москва"
  ],
  [
    "ООО «ЮМани»",
    "Москва"
  ]
]
STAGES=["DRAFT","STAFF_REVIEW","COORDINATOR_REVIEW","OFFICE_REVIEW","PARTNER_REVIEW","REVISION_REQUIRED","SIGNING_PENDING"]
with SessionLocal() as db:
    student_demo=db.scalar(select(User).where(User.username=="student-demo"))
    enterprise_demo=db.scalar(select(User).where(User.username=="enterprise-demo"))
    if not student_demo or not enterprise_demo:
        print("TEST29: pilot accounts missing, no changes")
    else:
        password=os.getenv("PILOT_PASSWORD_STUDENT","")
        if not password:
            print("TEST29: pilot student password missing, no changes")
        else:
            created=0
            for i in range(1,30):
                old_reference=f"TEST-CIFRA-2026-{i:03d}"
                student_name=f"{SURNAMES[i-1]} {NAMES[(i-1)%len(NAMES)]} {PATRONYMICS[(i-1)%len(PATRONYMICS)]}"
                reference=f"{SURNAMES[i-1]} {NAMES[(i-1)%len(NAMES)][0]}.{PATRONYMICS[(i-1)%len(PATRONYMICS)][0]}."
                existing=db.scalar(select(Agreement).where(Agreement.reference.in_([old_reference,reference])))
                login=f"cifra-test-student-{i:02d}"
                student=db.scalar(select(User).where(User.username==login))
                if not student:
                    student=User(username=login,full_name=student_name,role="STUDENT",
                        password_hash=hash_password(password),organization_name="")
                    db.add(student);db.flush()
                company,address=COMPANIES[i-1]
                if existing:
                    existing.status=STAGES[(i-1)%len(STAGES)]
                    existing.reference=reference
                    existing.company_name=company
                    existing.description='ТЕСТОВАЯ ЗАПИСЬ: вымышленный студент, организация из реестра практики 2026. Статус этапа смоделирован для проверки интерфейса; реального согласования и подписей нет.'
                    card=db.scalar(select(CompanyCard).where(CompanyCard.agreement_id==existing.id))
                    if card:
                        card.company_full_name=company
                        card.company_short_name=company
                        card.company_address=address
                        card.company_actual_address=address
                    continue
                agreement=Agreement(reference=reference,title=f"Практическая подготовка — {student_name}",
                    practice_type="Производственная",student_id=student.id,company_name=company,
                    enterprise_user_id=enterprise_demo.id,practice_start="2026-07-01",
                    practice_end="2026-07-28",status=STAGES[(i-1)%len(STAGES)],is_demo=True,
                    description="ТЕСТОВАЯ ЗАПИСЬ. Вымышленный студент; организация из реестра практики 2026. Статус этапа смоделирован; не является заключённым договором.")
                db.add(agreement);db.flush()
                db.add(CompanyCard(agreement_id=agreement.id,student_name=student_name,
                    group_name=f"ТЕСТ-26-{(i-1)//10+1}",course_name="3",
                    department="Учебное подразделение",program="Экономика и управление",
                    company_full_name=company,company_short_name=company,
                    company_address=address,
                    company_actual_address=address,
                    company_inn="",company_kpp="",company_ogrn="",
                    director_position="Руководитель",director_name="Не указано",
                    director_basis="Не указано",contact_name="Не указано",
                    contact_phone="",contact_email="",room_address="Не указано"))
                db.add(AuditEvent(actor_id=None,action="test_card_created",
                    entity_type="agreement",entity_id=agreement.id,
                    details="Создана демонстрационная карточка с вымышленными данными. Подписей нет."))
                created+=1
            db.commit()
            print(f"TEST29: synchronized 29 test cards with organizations from source Excel; newly created {created}")
