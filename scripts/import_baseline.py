"""Import de-identified baseline into the connected database. Run only on authorized datasets."""
import csv
from sqlalchemy import select
from app.database import Base, engine, SessionLocal
from app.models import BaselineObservation
from app.settings import ROOT

Base.metadata.create_all(engine)
path=ROOT/'data'/'baseline_103_anonymized.csv'
if not path.exists():raise SystemExit('Private de-identified data file is not available')
inserted=0
with SessionLocal() as db, path.open(encoding='utf-8-sig',newline='') as fh:
    for r in csv.DictReader(fh):
        if db.scalar(select(BaselineObservation.id).where(BaselineObservation.source_reference==r['source_reference'])):continue
        db.add(BaselineObservation(source_reference=r['source_reference'],practice_type=r['practice_type'],
          submitted_date=r['submitted_date'],forwarded_date=r['forwarded_date'],signed_date=r['signed_date'],
          campus_days=int(r['campus_days']),waiting_days=int(r['waiting_days']),visits=int(r['visits']),
          returns=int(r['returns']),lost=r['lost']=='1',evidence=r['evidence'],external_delay_days=int(r['external_delay_days'])))
        inserted+=1
    db.commit()
print(f'Baseline rows imported: {inserted} (existing records preserved).')