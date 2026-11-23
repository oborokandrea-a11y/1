"""Qualified signature verification gateway: fail closed unless trusted verifier configured.

The external verifier must validate CAdES-BES, chain/revocation, signing time,
certificate qualification, key usage and signer's authority. A browser result
or a locally computed digest is never sufficient evidence of УКЭП.
"""
import base64
import hashlib
import hmac
import json
from urllib.request import Request,urlopen
from .settings import settings

def verify_detached_cades(document:bytes,signature_b64:str,expected_role:str):
    if not settings.UKEP_VERIFY_URL or not settings.UKEP_VERIFY_TOKEN:
        raise RuntimeError("Сервис криптографической проверки УКЭП не подключён")
    if len(signature_b64)>2_000_000:
        raise ValueError("Подпись превышает допустимый размер")
    raw=base64.b64decode(signature_b64,validate=True)
    if not raw or len(raw)>1_500_000:
        raise ValueError("Неверный формат подписи")
    digest=hashlib.sha256(document).hexdigest()
    body=json.dumps({"document_base64":base64.b64encode(document).decode(),
                     "signature_base64":signature_b64,"document_sha256":digest,
                     "expected_role":expected_role},separators=(",",":")).encode()
    req=Request(settings.UKEP_VERIFY_URL,data=body,headers={
        "Authorization":"Bearer "+settings.UKEP_VERIFY_TOKEN,
        "Content-Type":"application/json"},method="POST")
    with urlopen(req,timeout=20) as response:
        if response.status!=200:raise RuntimeError("Ошибка сервиса проверки")
        result=json.load(response)
    # All these flags MUST be produced by a trusted cryptographic verifier.
    required=("signature_valid","document_hash_matches","chain_valid",
              "not_revoked","qualified_certificate","signer_authorized")
    if not all(result.get(k) is True for k in required):
        raise ValueError("УКЭП не прошла полную проверку")
    if result.get("document_sha256")!=digest:
        raise ValueError("Подписан другой документ")
    if result.get("expected_role")!=expected_role:
        raise ValueError("Подпись не соответствует роли подписанта")
    return {"document_sha256":digest,"certificate_subject":str(result.get("certificate_subject",""))[:400],
            "certificate_serial":str(result.get("certificate_serial",""))[:120],
            "certificate_issuer":str(result.get("certificate_issuer",""))[:400],
            "verification_time":str(result.get("verification_time",""))[:80]}
