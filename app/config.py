# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL")
    JWT_SECRET = os.getenv("JWT_SECRET")

    # Cloudflare R2
    R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
    R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
    R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "lumen-legal-files")

    # Backups automáticos
    BACKUP_AUTO_ENABLED = os.getenv("BACKUP_AUTO_ENABLED", "true").lower() == "true"
    BACKUP_AUTO_EVERY_DAYS = int(os.getenv("BACKUP_AUTO_EVERY_DAYS", "5"))
    BACKUP_AUTO_KEEP = int(os.getenv("BACKUP_AUTO_KEEP", "10"))


settings = Settings()