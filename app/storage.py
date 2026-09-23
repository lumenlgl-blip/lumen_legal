# app/storage.py
# Almacenamiento en Cloudflare R2 (S3-compatible)

import boto3
import os
import uuid
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.getenv("R2_BUCKET_NAME", "lumen-legal-files")

s3_client = boto3.client(
    service_name="s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)


def upload_bytes(data: bytes, folder: str, filename: str, content_type: str = "application/pdf") -> str:
    """Sube bytes a R2. Retorna la KEY (ruta dentro del bucket)."""
    key = f"{folder}/{filename}"
    s3_client.put_object(Bucket=R2_BUCKET, Key=key, Body=data, ContentType=content_type)
    return key


def upload_fileobj(file_obj, folder: str, filename: str, content_type: str = "application/pdf") -> str:
    """Sube UploadFile de FastAPI a R2. Retorna la KEY."""
    key = f"{folder}/{filename}"
    s3_client.upload_fileobj(
        file_obj, R2_BUCKET, key,
        ExtraArgs={"ContentType": content_type},
    )
    return key


def delete_file(key: str) -> bool:
    """Elimina un objeto de R2."""
    if not key:
        return False
    try:
        s3_client.delete_object(Bucket=R2_BUCKET, Key=key)
        return True
    except Exception as e:
        print(f"⚠️ Error al eliminar {key}: {e}")
        return False


def get_file_url(key: str, expires_in: int = 3600) -> str:
    """Genera URL prefirmada temporal."""
    if not key:
        return None
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def make_key(folder: str, filename: str) -> str:
    """Genera una key única con UUID para evitar colisiones."""
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
    return f"{folder}/{uuid.uuid4().hex}.{ext}"