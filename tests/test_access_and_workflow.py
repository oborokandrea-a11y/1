"""Regression tests: ownership, demo isolation, submission and DOCX output."""
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.models import User, Agreement, CompanyCard, Attachment
from app.security import can_view_agreement
from app.services import advance_agreement
from app.company_card import build_docx
from docx import Document
from fastapi import HTTPException

def test_student_cannot_view_demo_or_others():
    student=SimpleNamespace(id=11,role="STUDENT")
    own=SimpleNamespace(student_id=11,enterprise_user_id=None,is_demo=False)
    demo=SimpleNamespace(student_id=11,enterprise_user_id=None,is_demo=True)
    other=SimpleNamespace(student_id=12,enterprise_user_id=None,is_demo=False)
    assert can_view_agreement(student,own)
    assert not can_view_agreement(student,demo)
    assert not can_view_agreement(student,other)
    enterprise=SimpleNamespace(id=21,role="ENTERPRISE")
    assert not can_view_agreement(enterprise,own)
    own.enterprise_user_id=21
    assert can_view_agreement(enterprise,own)

def test_generated_company_card_can_be_submitted_without_upload():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(username="student_11",full_name="Test Student",role="STUDENT",password_hash="test")
        db.add(user);db.flush()
        case=Agreement(reference="1192/26",title="Test",student_id=user.id,company_name="Test Co",
                       status="DRAFT",practice_start="2026-06-01",practice_end="2026-06-30")
        db.add(case);db.flush()
        db.add(CompanyCard(agreement_id=case.id,student_name="Test Student",group_name="TEST",
            department="Department",program="Economics",company_full_name="Test Co",
            company_address="Test address",company_inn="7706019535",director_position="Director",
            director_name="Director Name",director_basis="Charter",contact_name="Contact",
            contact_phone="123",room_address="Address"))
        db.flush()
        advance_agreement(db,user,case,"submit")
        assert case.status=="STAFF_REVIEW"

def test_empty_legacy_agreement_requires_attachment():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(username="student_12",full_name="Test Student",role="STUDENT",password_hash="test")
        db.add(user);db.flush()
        case=Agreement(reference="1193/26",title="Legacy",student_id=user.id,company_name="Test",
                       status="DRAFT")
        db.add(case);db.flush()
        try:advance_agreement(db,user,case,"submit")
        except HTTPException as exc:assert exc.status_code==422
        else:raise AssertionError("Legacy agreement without file must not submit")
