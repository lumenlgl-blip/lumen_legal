# create_user_manual.py
from app.database import SessionLocal
from app.models.core import User, Firm
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
db = SessionLocal()

# Verificar firma
firm = db.query(Firm).first()
if not firm:
    firm = Firm(name="Lumen Legal")
    db.add(firm)
    db.commit()
    db.refresh(firm)
    print(f"✅ Firma creada: ID {firm.id}")
else:
    print(f"ℹ️ Firma ya existe: ID {firm.id}")

# Eliminar usuario admin existente (si hay)
existing = db.query(User).filter(User.email == "admin@lumenlegal.com").first()
if existing:
    db.delete(existing)
    db.commit()
    print("✅ Usuario anterior eliminado")

# Crear nuevo usuario admin
password = "admin123"
hashed = pwd_context.hash(password)

new_user = User(
    firm_id=firm.id,
    full_name="Administrador",
    email="admin@lumenlegal.com",
    hashed_password=hashed,
    role="admin",
    is_active=True,
    must_change_password=False,  # El admin no necesita cambiar contraseña
    permissions="all"  # Acceso total
)
db.add(new_user)
db.commit()
db.refresh(new_user)

print(f"✅ Usuario creado exitosamente:")
print(f"   📧 Email: admin@lumenlegal.com")
print(f"   🔑 Contraseña: admin123")
print(f"   👤 Rol: admin")
print(f"   🔐 Permisos: all")
print(f"   🆔 ID: {new_user.id}")

db.close()