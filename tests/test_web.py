"""Regression tests for the real role-based web flow and original sprint checklist."""
import os
import re
import tempfile
from pathlib import Path

ROOT=Path(tempfile.mkdtemp(prefix='cifra_tests_'))
os.environ['DATABASE_URL']='sqlite:///'+str(ROOT/'tests.sqlite3')
os.environ['UPLOAD_DIR']=str(ROOT/'private')
os.environ['SECRET_KEY']='testing-secret-key-is-long-and-not-for-prod'
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.database import Base,engine,SessionLocal
from app.main import app
from app.models import User,Attachment,Agreement,AuditEvent,Task
from app.security import hash_password

TEST_PASSWORD='SecureTestPass2026!'
USERS=[('student1','STUDENT'),('student2','STUDENT'),('staff1','STAFF'),('coordinator1','COORDINATOR'),('office1','OFFICE'),('partner1','ENTERPRISE'),('manager1','MANAGER')]

def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for username,role in USERS:
            if db.scalar(select(User.id).where(User.username==username)):continue
            db.add(User(username=username,role=role,full_name=username+' user',password_hash=hash_password(TEST_PASSWORD),organization_name='Example Org'))
        db.commit()

def csrf(client,path='/login'):
    r=client.get(path)
    assert r.status_code==200,(r.status_code,r.text[:400])
    m=re.search(r'name="csrf_token" value="([^"]+)"',r.text)
    assert m, r.text[:200]
    return m.group(1)

def auth(client,username,role):
    token=csrf(client)
    response=client.post('/login',data={'username':username,'password':TEST_PASSWORD,'realm':role,'csrf_token':token},follow_redirects=False)
    assert response.status_code==303,response.text[:200]
    assert response.headers['location']=='/dashboard',response.headers['location']
    assert client.get('/dashboard').status_code==200

def logout(client):
    token=csrf(client,'/profile')
    assert client.post('/logout',data={'csrf_token':token},follow_redirects=False).status_code==303

def new_case(client):
    token=csrf(client,'/agreements/new')
    r=client.post('/agreements/new',data={'csrf_token':token,'title':'Договор о производственной практике','company_name':'ООО Тестовый партнёр',
      'practice_type':'Производственная','deadline_at':'2026-12-03','signing_order':'university_first'},follow_redirects=False)
    assert r.status_code==303,r.text[:300]
    return int(r.headers['location'].split('/')[-1])

def test_full_workflow():
    init_db()
    with TestClient(app) as c:
        assert c.get('/').status_code==200
        assert 'Согласование документов' in c.get('/').text
        assert c.get('/dashboard',follow_redirects=False).status_code==303
        auth(c,'student1','STUDENT')
        agree_id=new_case(c)
        assert f'CF-' in c.get(f'/agreements/{agree_id}').text
        nofile=c.post(f'/agreements/{agree_id}/transition',data={'csrf_token':csrf(c,f'/agreements/{agree_id}'),'action':'submit'},follow_redirects=False)
        assert nofile.status_code==422
        bad=c.post(f'/agreements/{agree_id}/upload',data={'csrf_token':csrf(c,f'/agreements/{agree_id}')},files={'file':('payload.pdf',b'not a pdf','application/pdf')},follow_redirects=False)
        assert bad.status_code==303
        with SessionLocal() as db: assert not db.scalar(select(Attachment.id).where(Attachment.agreement_id==agree_id))
        uploaded=c.post(f'/agreements/{agree_id}/upload',data={'csrf_token':csrf(c,f'/agreements/{agree_id}')},files={'file':('договор.pdf',b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF','application/pdf')},follow_redirects=False)
        assert uploaded.status_code==303
        with SessionLocal() as db:
            attachment=db.scalar(select(Attachment).where(Attachment.agreement_id==agree_id))
            assert attachment and attachment.version_no==1 and len(attachment.sha256)==64
            file_id=attachment.id
        r=c.get(f'/files/{file_id}/download');assert r.status_code==200 and r.content.startswith(b'%PDF')
        token=csrf(c,f'/agreements/{agree_id}')
        assert c.post(f'/agreements/{agree_id}/transition',data={'csrf_token':token,'action':'submit'},follow_redirects=False).status_code==303
        with SessionLocal() as db: assert db.get(Agreement,agree_id).status=='STAFF_REVIEW'
        assert c.post(f'/agreements/{agree_id}/transition',data={'csrf_token':csrf(c,f'/agreements/{agree_id}'),'action':'staff_approve'},follow_redirects=False).status_code==403
        logout(c)
    with TestClient(app) as intruder:
        auth(intruder,'student2','STUDENT')
        assert intruder.get(f'/agreements/{agree_id}').status_code==403
        assert intruder.get(f'/files/{file_id}/download').status_code==403
        assert intruder.post(f'/agreements/{agree_id}/transition',data={'action':'submit'},follow_redirects=False).status_code==403
    for role,name,action,expected in [
         ('STAFF','staff1','staff_approve','COORDINATOR_REVIEW'),
         ('COORDINATOR','coordinator1','coordinator_approve','OFFICE_REVIEW'),
         ('OFFICE','office1','office_approve','PARTNER_REVIEW')]:
        with TestClient(app) as c:
            auth(c,name,role)
            token=csrf(c,f'/agreements/{agree_id}')
            assert c.post(f'/agreements/{agree_id}/transition',data={'csrf_token':token,'action':action},follow_redirects=False).status_code==303
            with SessionLocal() as db: assert db.get(Agreement,agree_id).status==expected
    with SessionLocal() as db:
        case=db.get(Agreement,agree_id)
        case.enterprise_user_id=db.scalar(select(User.id).where(User.username=='partner1'))
        db.commit()
    with TestClient(app) as c:
        auth(c,'partner1','ENTERPRISE')
        assert c.get(f'/agreements/{agree_id}').status_code==200
        assert c.post(f'/agreements/{agree_id}/transition',data={'csrf_token':csrf(c,f'/agreements/{agree_id}'),'action':'partner_approve'},follow_redirects=False).status_code==303
    with SessionLocal() as db:
        case=db.get(Agreement,agree_id)
        assert case.status=='COMPLETED' and case.completed_at
        assert db.scalar(select(Attachment).where(Attachment.agreement_id==agree_id)).version_no==1
        logs=db.scalars(select(AuditEvent).where(AuditEvent.entity_id==agree_id,AuditEvent.entity_type=='agreement')).all()
        assert len(logs)>=6

def test_tasks_and_auth():
    init_db()
    with TestClient(app) as c:
        auth(c,'manager1','MANAGER')
        assert c.get('/analytics').status_code==200
        assert c.get('/activity').status_code==200
        assert c.get('/tasks').status_code==200
        with SessionLocal() as db: person=db.scalar(select(User).where(User.username=='coordinator1'))
        r=c.post('/tasks/new',data={'csrf_token':csrf(c,'/tasks'),'title':'Проверить документы практики','assigned_to_id':str(person.id),'priority':'high','due_at':'2026-12-20'},follow_redirects=False)
        assert r.status_code==303
        with SessionLocal() as db:
            t=db.scalar(select(Task).where(Task.title=='Проверить документы практики'));assert t and t.due_at and t.priority=='high'
            task_id=t.id
        assert c.post(f'/tasks/{task_id}/state',data={'csrf_token':csrf(c,'/tasks'),'state':'DONE'},follow_redirects=False).status_code==303
        with SessionLocal() as db:assert db.get(Task,task_id).status=='DONE'
        # Wrong CSRF must not create tasks
        assert c.post('/tasks/new',data={'csrf_token':'bad','title':'unwanted','assigned_to_id':str(person.id)},follow_redirects=False).status_code==403
    with TestClient(app) as c:
        auth(c,'student1','STUDENT')
        assert c.get('/analytics').status_code==403
        assert c.get('/activity').status_code==403
        assert c.get('/partners').status_code==403
        assert c.get('/team').status_code==403
        assert c.post('/tasks/new',data={},follow_redirects=False).status_code==403

def test_enterprise_invitation_one_time():
    init_db()
    with TestClient(app) as c:
        auth(c,'student2','STUDENT')
        case_id=new_case(c)
    with TestClient(app) as c:
        auth(c,'office1','OFFICE')
        result=c.post('/partners/invite',data={'csrf_token':csrf(c,'/partners'),'agreement_id':str(case_id),'organization_name':'ООО Пилот'},follow_redirects=True)
        assert result.status_code==200
        m=re.search(r'/activate/[A-Za-z0-9_\-]+',result.text)
        assert m,'No one-time activation link in partner screen'
        activation=m.group(0)
    with TestClient(app) as c:
        assert c.get(activation).status_code==200
        resp=c.post(activation,data={'csrf_token':csrf(c,activation),'username':'partner.new','full_name':'Уполномоченный представитель','password':TEST_PASSWORD},follow_redirects=False)
        assert resp.status_code==303
        assert c.get(activation).status_code==200 and 'недоступно' in c.get(activation).text.lower()
        assert c.post(activation,data={'username':'other','password':TEST_PASSWORD,'full_name':'another'},follow_redirects=False).status_code==403
        auth(c,'partner.new','ENTERPRISE')
        assert c.get(f'/agreements/{case_id}').status_code==200
        with SessionLocal() as db:
            not_owned=db.scalar(select(Agreement).where(Agreement.student_id!=db.get(Agreement,case_id).student_id).limit(1))
            if not_owned:assert c.get(f'/agreements/{not_owned.id}').status_code==403

def test_revision_attachments_and_reverse_signing_order():
    init_db()
    with TestClient(app) as student:
        auth(student,'student1','STUDENT')
        token=csrf(student,'/agreements/new')
        created=student.post('/agreements/new',data={'csrf_token':token,'title':'Производственная практика — обратное согласование','company_name':'ООО Проверка','signing_order':'enterprise_first'},follow_redirects=False)
        assert created.status_code==303
        case_id=int(created.headers['location'].split('/')[-1])
        uploaded=student.post(f'/agreements/{case_id}/upload',files={'file':('v1.pdf',b'%PDF-1.4\noriginal\n%%EOF','application/pdf')},data={'csrf_token':csrf(student,f'/agreements/{case_id}')},follow_redirects=False)
        assert uploaded.status_code==303
        student.post(f'/agreements/{case_id}/transition',data={'csrf_token':csrf(student,f'/agreements/{case_id}'),'action':'submit'},follow_redirects=False)
    with TestClient(app) as staff:
        auth(staff,'staff1','STAFF')
        response=staff.post(f'/agreements/{case_id}/transition',data={'csrf_token':csrf(staff,f'/agreements/{case_id}'),'action':'return'},follow_redirects=False)
        assert response.status_code==303
    with TestClient(app) as student:
        auth(student,'student1','STUDENT')
        assert 'На доработке' in student.get(f'/agreements/{case_id}').text
        second=student.post(f'/agreements/{case_id}/upload',files={'file':('v2.docx',b'PK\x03\x04\x14\x00new_version','application/vnd.openxmlformats-officedocument.wordprocessingml.document')},data={'csrf_token':csrf(student,f'/agreements/{case_id}')},follow_redirects=False)
        assert second.status_code==303
        with SessionLocal() as db:
            files=db.scalars(select(Attachment).where(Attachment.agreement_id==case_id).order_by(Attachment.version_no)).all()
            assert len(files)==2 and [a.version_no for a in files]==[1,2]
        assert student.post(f'/agreements/{case_id}/transition',data={'csrf_token':csrf(student,f'/agreements/{case_id}'),'action':'submit'},follow_redirects=False).status_code==303
    for username,role,action,expected in [('staff1','STAFF','staff_approve','COORDINATOR_REVIEW'),('coordinator1','COORDINATOR','coordinator_approve','PARTNER_REVIEW')]:
        with TestClient(app) as client:
            auth(client,username,role)
            client.post(f'/agreements/{case_id}/transition',data={'csrf_token':csrf(client,f'/agreements/{case_id}'),'action':action},follow_redirects=False)
            with SessionLocal() as db:assert db.get(Agreement,case_id).status==expected
    with SessionLocal() as db:
        obj=db.get(Agreement,case_id);obj.enterprise_user_id=db.scalar(select(User.id).where(User.username=='partner1'));db.commit()
    for username,role,action,expected in [('partner1','ENTERPRISE','partner_approve','OFFICE_REVIEW'),('office1','OFFICE','office_approve','COMPLETED')]:
        with TestClient(app) as client:
            auth(client,username,role)
            client.post(f'/agreements/{case_id}/transition',data={'csrf_token':csrf(client,f'/agreements/{case_id}'),'action':action},follow_redirects=False)
            with SessionLocal() as db:assert db.get(Agreement,case_id).status==expected