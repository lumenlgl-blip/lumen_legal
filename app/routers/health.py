from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter(tags=["System"])

@router.get("/ping")
def ping():
    return {"message": "Lumen Legal API está viva"}

@router.get("/db-test")
def test_db(db: Session = Depends(get_db)):
    # Intenta hacer un query simple a la tabla users (aunque esté vacía)
    from app.models.core import User
    count = db.query(User).count()
    return {"status": "Conectado a la BD", "users_count": count}