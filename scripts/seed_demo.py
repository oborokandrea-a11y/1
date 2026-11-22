"""Create LOCAL sample accounts and baseline observations. Not for public production.
Run: python -m scripts.seed_demo
"""
import csv
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import select

from app.database import engine, Base, SessionLocal
from app.settings import ROOT, settings
from app.models import User, Agreement, AuditEvent, Task, BaselineObservation
from app.security import hash_password
from app.services import log_event

if settings.production:
    raise SystemExit('Refusing to install demo credentials into production')
Base.metadata.create_all(engine)
role_accounts=[
    ('student.demo', 'Екатерина Смирнова', 'STUDENT'),
    ('staff.demo', 'Илья Волков', 'STAFF'),
    ('practice.demo', 'Мария Соколова', 'COORDINATOR'),
    ('office.demo', 'Светлана Орлова', 'OFFICE'),
    ('partner.demo', 'Представитель компании', 'ENTERPRISE'),
    ('manager.demo', 'Руководитель проекта', 'MANAGER'),
]

with SessionLocal() as db:
    creds=[]
    accounts={}
    for username, full_name, role in role_accounts:
        user=db.scalar(select(User).where(User.username==username))
        if not user:
            pwd=secrets.token_urlsafe(15)+'aA1'
            user=User(username=username,full_name=full_name,role=role,password_hash=hash_password(pwd),
                organization_name='Учебная организация' if role!='ENTERPRISE' else 'АО «Образец»')
            db.add(user);db.flush()
            creds.append((username,role,pwd))
        accounts[role]=user
    db.commit()
    with (ROOT/'data'/'baseline_103_anonymized.csv').open('r',encoding='utf-8-sig',newline='') as f:
        for item in csv.DictReader(f):
            if db.scalar(select(BaselineObservation.id).where(BaselineObservation.source_reference==item['source_reference'])):
                continue
            db.add(BaselineObservation(
                source_reference=item['source_reference'],practice_type=item['practice_type'],
                submitted_date=item['submitted_date'], forwarded_date=item['forwarded_date'],signed_date=item['signed_date'],
                campus_days=int(item['campus_days']),waiting_days=int(item['waiting_days']),
                visits=int(item['visits']),returns=int(item['returns']),lost=item['lost']=='1',
                evidence=item['evidence'],external_delay_days=int(item['external_delay_days'])))
    db.commit()
    if not db.scalar(select(Agreement.id).limit(1)):
        demo=[
            Agreement(reference='CF-2026-00001',title='Договор о производственной практике',practice_type='Производственная',
                student_id=accounts['STUDENT'].id,enterprise_user_id=accounts['ENTERPRISE'].id,
                company_name='АО «Образец»',practice_start='2026-12-01',practice_end='2026-12-22',
                signing_order='university_first',status='DRAFT',is_demo=True,
                deadline_at=datetime.utcnow()+timedelta(days=6),description='Демонстрационный договор. Данные вымышлены, персональные сведения не используются.'),
            Agreement(reference='CF-2026-00002',title='Проверка учебной практики',practice_type='Учебная',
                student_id=accounts['STUDENT'].id,enterprise_user_id=accounts['ENTERPRISE'].id,
                company_name='АО «Образец»',practice_start='2026-12-03',practice_end='2026-12-17',
                signing_order='enterprise_first',status='STAFF_REVIEW',is_demo=True,submitted_at=datetime.utcnow()-timedelta(days=1),
                deadline_at=datetime.utcnow()+timedelta(days=4),description='Тестовый процесс с обратным порядком подписания.'),
        ]
        for obj in demo:db.add(obj)
        db.flush()
        for item in demo:log_event(db,accounts['MANAGER'],'demo_created','agreement',item.id,'Синтетический демонстрационный документ, не исходное измерение')
        db.add(Task(title='Проверить комплектность договора',assigned_to_id=accounts['COORDINATOR'].id,
            created_by_id=accounts['MANAGER'].id,agreement_id=demo[1].id,
            due_at=datetime.utcnow()+timedelta(days=3),priority='high'))
    db.commit()

if creds:
    target=ROOT/'demo_credentials.txt'
    lines=['ЦИФРА — ВРЕМЕННЫЕ ДЕМО-ДОСТУПЫ','Для локального тестирования. НЕ ПУБЛИКОВАТЬ файл и не использовать данные логины в production.','']
    lines += [f'{name} | {role} | {pwd}' for name,role,pwd in creds]
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    try:os.chmod(target,0o600)
    except OSError:pass
    print(f'6 role accounts initialized; credentials saved: {target}')
else: print('Existing users preserved; no passwords modified')
print('Baseline observations: 103 anonymized rows, imported where absent.')