from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def now_utc():
    return datetime.utcnow()

class User(Base):
    __tablename__="users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(90), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    role: Mapped[str] = mapped_column(String(40), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    organization_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

class Agreement(Base):
    __tablename__="agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(230))
    practice_type: Mapped[str] = mapped_column(String(70), default="Производственная")
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    student: Mapped[User] = relationship(foreign_keys=[student_id])
    company_name: Mapped[str] = mapped_column(String(210))
    enterprise_user_id: Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    enterprise_user: Mapped[User|None] = relationship(foreign_keys=[enterprise_user_id])
    practice_start: Mapped[str] = mapped_column(String(10), default="")
    practice_end: Mapped[str] = mapped_column(String(10), default="")
    deadline_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    signing_order: Mapped[str] = mapped_column(String(32), default="university_first")
    status: Mapped[str] = mapped_column(String(45), default="DRAFT", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
    submitted_at: Mapped[datetime|None] = mapped_column(DateTime)
    staff_approved_at: Mapped[datetime|None] = mapped_column(DateTime)
    coordinator_approved_at: Mapped[datetime|None] = mapped_column(DateTime)
    office_approved_at: Mapped[datetime|None] = mapped_column(DateTime)
    partner_approved_at: Mapped[datetime|None] = mapped_column(DateTime)
    completed_at: Mapped[datetime|None] = mapped_column(DateTime)

class Attachment(Base):
    __tablename__="attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(240))
    object_key: Mapped[str] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    version_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    uploader: Mapped[User] = relationship(foreign_keys=[uploaded_by_id])

class AgreementNote(Base):
    __tablename__="agreement_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    author: Mapped[User] = relationship(foreign_keys=[author_id])

class Task(Base):
    __tablename__="tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(230))
    details: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agreement_id: Mapped[int|None] = mapped_column(ForeignKey("agreements.id"), nullable=True)
    due_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(30), default="TODO")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
    assignee: Mapped[User] = relationship(foreign_keys=[assigned_to_id])
    creator: Mapped[User] = relationship(foreign_keys=[created_by_id])

class AuditEvent(Base):
    __tablename__="audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(75))
    entity_type: Mapped[str] = mapped_column(String(65))
    entity_id: Mapped[int] = mapped_column(Integer)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    actor: Mapped[User|None] = relationship(foreign_keys=[actor_id])

class PartnerInvite(Base):
    __tablename__="partner_invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    organization_name: Mapped[str] = mapped_column(String(210))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    redeemed_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

class BaselineObservation(Base):
    __tablename__="baseline_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_reference: Mapped[str] = mapped_column(String(65), unique=True)
    practice_type: Mapped[str] = mapped_column(String(80))
    submitted_date: Mapped[str] = mapped_column(String(10))
    forwarded_date: Mapped[str] = mapped_column(String(10))
    signed_date: Mapped[str] = mapped_column(String(10))
    campus_days: Mapped[int] = mapped_column(Integer)
    waiting_days: Mapped[int] = mapped_column(Integer)
    visits: Mapped[int] = mapped_column(Integer)
    returns: Mapped[int] = mapped_column(Integer)
    lost: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str] = mapped_column(String(60))
    external_delay_days: Mapped[int] = mapped_column(Integer, default=0)

class LoginAttempt(Base):
    __tablename__="login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(90), index=True)
    remote_address: Mapped[str] = mapped_column(String(70), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

class CompanyCard(Base):
    __tablename__ = "company_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), unique=True, index=True)
    student_name: Mapped[str] = mapped_column(String(180))
    group_name: Mapped[str] = mapped_column(String(90))
    course_name: Mapped[str] = mapped_column(String(50))
    department: Mapped[str] = mapped_column(String(180))
    program: Mapped[str] = mapped_column(String(200))
    company_full_name: Mapped[str] = mapped_column(String(250))
    company_address: Mapped[str] = mapped_column(String(450))
    company_inn: Mapped[str] = mapped_column(String(15))
    company_kpp: Mapped[str] = mapped_column(String(15), default="")
    company_ogrn: Mapped[str] = mapped_column(String(20), default="")
    company_okpo: Mapped[str] = mapped_column(String(20), default="")
    company_okved: Mapped[str] = mapped_column(String(30), default="")
    director_position: Mapped[str] = mapped_column(String(120))
    director_name: Mapped[str] = mapped_column(String(180))
    director_basis: Mapped[str] = mapped_column(String(180))
    contact_name: Mapped[str] = mapped_column(String(180))
    contact_phone: Mapped[str] = mapped_column(String(60))
    contact_email: Mapped[str] = mapped_column(String(180))
    room_department: Mapped[str] = mapped_column(String(180), default="")
    room_address: Mapped[str] = mapped_column(String(450))
    practice_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    university_contact: Mapped[str] = mapped_column(String(180), default="")
    university_phone: Mapped[str] = mapped_column(String(60), default="")
    university_email: Mapped[str] = mapped_column(String(180), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
