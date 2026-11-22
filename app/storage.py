"""All uploads are private: never expose an uploads directory via StaticFiles."""
import io
import pathlib
import uuid
import boto3
from .settings import settings, UPLOAD_ROOT

EXT_MIME={
 ".pdf":"application/pdf",
 ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
 ".png":"image/png",
 ".jpg":"image/jpeg",
 ".jpeg":"image/jpeg",
}

def validate_file(filename:str, data:bytes):
    ext=pathlib.Path(filename or "").suffix.lower()
    if ext not in EXT_MIME:
        raise ValueError("Допускаются только PDF, DOCX, PNG и JPG")
    if not data or len(data)>settings.MAX_UPLOAD_MB*1024*1024:
        raise ValueError(f"Размер файла: от 1 байта до {settings.MAX_UPLOAD_MB} МБ")
    sig={".pdf":b"%PDF", ".docx":b"PK\x03\x04", ".png":b"\x89PNG", ".jpg":b"\xff\xd8\xff", ".jpeg":b"\xff\xd8\xff"}[ext]
    if not data.startswith(sig):
        raise ValueError("Файл не соответствует разрешённому формату")
    return ext,EXT_MIME[ext]

def safe_filename(filename:str)->str:
    p=pathlib.Path(filename or "документ")
    return ("".join(ch for ch in p.name if ch.isalnum() or ch in "-_ .()") or "документ")[:220]

def s3_client():
    options={}
    if settings.S3_ENDPOINT_URL: options["endpoint_url"]=settings.S3_ENDPOINT_URL
    if settings.S3_REGION: options["region_name"]=settings.S3_REGION
    if settings.AWS_ACCESS_KEY_ID: options["aws_access_key_id"]=settings.AWS_ACCESS_KEY_ID
    if settings.AWS_SECRET_ACCESS_KEY: options["aws_secret_access_key"]=settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3",**options)

def save_file(agreement_id:int, filename:str, data:bytes, mime_type:str)->str:
    ext=pathlib.Path(filename).suffix.lower()
    key=f"agreements/{agreement_id}/{uuid.uuid4().hex}{ext}"
    if settings.S3_BUCKET:
        s3_client().put_object(Bucket=settings.S3_BUCKET,Key=key,Body=data,ContentType=mime_type,ServerSideEncryption="AES256")
    else:
        target=UPLOAD_ROOT/key
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    return key

def get_file(key:str)->bytes:
    if settings.S3_BUCKET:
        return s3_client().get_object(Bucket=settings.S3_BUCKET,Key=key)["Body"].read()
    base=UPLOAD_ROOT.resolve()
    path=(base/key).resolve()
    if not path.is_relative_to(base):
        raise FileNotFoundError("Invalid storage key")
    return path.read_bytes()