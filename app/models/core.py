from sqlalchemy import Column, Integer, String, Text, Date, Numeric, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime

# --- Modelo de la firma ---
class Firm(Base):
    __tablename__ = "firms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    logo_url = Column(String(500), nullable=True)
    privacy_notice = Column(Text, nullable=True)

# --- Modelo de Usuario ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    role = Column(String(20), default="abogado")
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)  # ← Nuevo
    permissions = Column(String(500), default="")  # ← Nuevo: lista separada por comas
    created_at = Column(DateTime, default=datetime.utcnow)

# --- Modelo de Cliente ---
class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    
    folio_registro = Column(String(20), unique=True, nullable=False)
    expediente_interno = Column(Integer, nullable=False)
    
    name = Column(String(50), nullable=False)
    paterno = Column(String(50), nullable=False)
    materno = Column(String(50), nullable=False)
    curp = Column(String(18), unique=True, nullable=False)
    phone = Column(String(15), nullable=False)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=False)
    occupation = Column(String(100), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    documents = relationship("ClientDocument", back_populates="client", cascade="all, delete-orphan")
    contracts = relationship("Contract", back_populates="client", cascade="all, delete-orphan")

# --- Documentos del cliente ---
class ClientDocument(Base):
    __tablename__ = "client_documents"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    doc_type = Column(String(20), nullable=False)
    file_url = Column(String(500), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    client = relationship("Client", back_populates="documents")

# --- Contrato (servicio contratado) ---
class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    
    # Tipo de servicio: asesoria, juicio, escrito, contestacion, diligencia
    service_type = Column(String(30), nullable=False)  # ← AHORA VA AQUÍ
    
    # Tipo de juicio (si aplica): Divorcio Bilateral, etc.
    tipo_juicio = Column(String(100), nullable=True)
    tipo_juicio_otro = Column(String(100), nullable=True)
    
    # Detalle específico
    specific_detail = Column(String(200), nullable=True)
    
    total_cost = Column(Numeric(10,2), nullable=False)
    status = Column(String(20), default="pendiente")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    client = relationship("Client", back_populates="contracts")
    court_case = relationship("CourtCase", uselist=False, back_populates="contract")
    payments = relationship("Payment", back_populates="contract", cascade="all, delete-orphan")

# --- Expediente del tribunal ---
class CourtCase(Base):
    __tablename__ = "court_cases"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    
    tribunal = Column(String(100), nullable=False)
    secretaria = Column(String(100), nullable=False)
    num_exp_tribunal = Column(String(50), unique=True, nullable=False)
    folio_tribunal = Column(String(50), nullable=False)
    fecha_presentacion = Column(Date, nullable=False)
    acuse_pdf_url = Column(String(500), nullable=True)
    
    demandado_nombre = Column(String(200), nullable=True)
    actor_nombre = Column(String(200), nullable=True)
    
    status = Column(String(30), default="iniciado")
    
    contract = relationship("Contract", back_populates="court_case")
    actuaciones = relationship("Actuacion", back_populates="court_case", cascade="all, delete-orphan")
    agenda_events = relationship("AgendaEvent", back_populates="court_case", cascade="all, delete-orphan")

# --- Pagos ---
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    
    amount = Column(Numeric(10,2), nullable=False)
    payment_date = Column(DateTime, default=datetime.utcnow)
    method = Column(String(20), nullable=False)
    receiver_name = Column(String(100), nullable=True)
    receipt_pdf_url = Column(String(500), nullable=True)
    
    contract = relationship("Contract", back_populates="payments")

# --- Actuaciones ---
class Actuacion(Base):
    __tablename__ = "actuaciones"
    id = Column(Integer, primary_key=True, index=True)
    court_case_id = Column(Integer, ForeignKey("court_cases.id"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    
    tipo = Column(String(30), nullable=False)
    fecha_actuacion = Column(Date, nullable=False)
    descripcion = Column(String(200), nullable=True)
    pdf_url = Column(String(500), nullable=False)
    
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    court_case = relationship("CourtCase", back_populates="actuaciones")
    
    # --- Agenda Judicial ---
class AgendaEvent(Base):
    __tablename__ = "agenda_events"
    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    court_case_id = Column(Integer, ForeignKey("court_cases.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    title = Column(String(200), nullable=False)
    event_type = Column(String(30), nullable=False)  # audiencia, plazos, junta, otro
    description = Column(Text, nullable=True)
    event_date = Column(Date, nullable=False)
    event_time = Column(String(10), nullable=True)  # HH:MM
    location = Column(String(200), nullable=True)
    reminder_days = Column(Integer, default=1)
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 🆕 NUEVO CAMPO: expediente manual (texto libre)
    expediente_manual = Column(String(100), nullable=True)
    
    court_case = relationship("CourtCase", back_populates="agenda_events")
    
    # --- Bitácora de Actividades ---
# --- Bitácora de Actividades ---
class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(50), nullable=False)
    entity = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)