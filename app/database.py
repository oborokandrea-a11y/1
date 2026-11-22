from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import settings

uri=settings.DATABASE_URL
if uri.startswith("postgres://"):
    uri="postgresql+psycopg://"+uri[len("postgres://"):]
elif uri.startswith("postgresql://"):
    uri="postgresql+psycopg://"+uri[len("postgresql://"):]
engine=create_engine(uri, pool_pre_ping=True, connect_args={"check_same_thread":False} if uri.startswith("sqlite") else {})
SessionLocal=sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
class Base(DeclarativeBase):
    pass

def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()