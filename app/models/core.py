from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Date,
    Numeric,
    ForeignKey,
    Boolean,
    DateTime,
    BigInteger,
)


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
    must_change_password = Column(Boolean, default=False)
    permissions = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# --- Modelo de Abogado ---
class Abogado(Base):
    __tablename__ = "abogados"
    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False, index=True)

    nombre_completo = Column(String(200), nullable=False)
    cedula_profesional = Column(String(50), nullable=False)
    especialidad = Column(String(150), nullable=True)
    telefono = Column(String(20), nullable=True)
    email = Column(String(120), nullable=True)
    domicilio_profesional = Column(String(300), nullable=False)

    # Vinculación opcional con un usuario del sistema (para login)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relación con el usuario (si está vinculado)
    user = relationship("User", foreign_keys=[user_id], uselist=False)


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

    documents = relationship(
        "ClientDocument", back_populates="client", cascade="all, delete-orphan"
    )
    contracts = relationship(
        "Contract", back_populates="client", cascade="all, delete-orphan"
    )


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
    service_type = Column(String(30), nullable=False)

    # Tipo de juicio (si aplica): Divorcio Bilateral, etc.
    tipo_juicio = Column(String(100), nullable=True)
    tipo_juicio_otro = Column(String(100), nullable=True)

    # Detalle específico
    specific_detail = Column(String(200), nullable=True)

    total_cost = Column(Numeric(10, 2), nullable=False)
    status = Column(String(20), default="pendiente")
    created_at = Column(DateTime, default=datetime.utcnow)

    # ── Abogado asignado (ahora apunta a la tabla abogados) ──
    assigned_lawyer_id = Column(Integer, ForeignKey("abogados.id"), nullable=True)
    assigned_lawyer = relationship("Abogado", foreign_keys=[assigned_lawyer_id])

    # ── Ciudad de suscripción del contrato ──
    ciudad_suscripcion = Column(String(100), nullable=True)

    # ── Honorarios en parcialidades ──
    monto_anticipo = Column(Numeric(12, 2), nullable=True)
    monto_saldo = Column(Numeric(12, 2), nullable=True)

    # ── Condiciones de exigibilidad (JSON array) ──
    # Ej: '["conclusion_procedimiento", "desistimiento", "revocacion"]'
    condiciones_exigibilidad = Column(Text, nullable=True)

    # ── Modalidad de pago del saldo ──
    # Valores: 'audiencia'  → liquidación única en primera audiencia
    #          'abonos'     → dos abonos hasta el dictado de sentencia
    modalidad_pago_saldo = Column(String(20), nullable=True, default="audiencia")

    # ── Datos específicos para contestación de demanda ──
    num_expediente_contestar = Column(String(80), nullable=True)
    tribunal_contestar = Column(String(200), nullable=True)
    actor_nombre = Column(String(200), nullable=True)
    actor2_nombre = Column(
        String(200), nullable=True
    )  # 2° promovente (divorcio bilateral)
    demandado_nombre = Column(String(200), nullable=True)
    fecha_notificacion_demanda = Column(Date, nullable=True)
    fecha_vencimiento_plazo = Column(Date, nullable=True)
    requiere_reconvencion = Column(Boolean, default=False)

    # ── Trazabilidad documental ──
    contrato_pdf_key = Column(String(500), nullable=True)
    contrato_pdf_hash = Column(String(64), nullable=True)
    contrato_firmado_key = Column(String(500), nullable=True)

    reconocimiento_pdf_key = Column(String(500), nullable=True)
    reconocimiento_pdf_hash = Column(String(64), nullable=True)
    reconocimiento_firmado_key = Column(String(500), nullable=True)

    pagare_pdf_key = Column(String(500), nullable=True)
    pagare_pdf_hash = Column(String(64), nullable=True)
    pagare_firmado_key = Column(String(500), nullable=True)

    # ── Constancia de actualización de datos procesales (solo inicio de juicio) ──
    constancia_actualizacion_firmado_key = Column(String(500), nullable=True)

    # ── Estado del flujo documental ──
    # Valores: sin_contrato | contrato_generado | contrato_firmado |
    #          saldo_exigible | reconocimiento_generado | reconocimiento_firmado |
    #          pagare_generado | pagare_firmado | liquidado
    estado_documental = Column(String(40), default="sin_contrato")

    client = relationship("Client", back_populates="contracts")
    court_case = relationship("CourtCase", uselist=False, back_populates="contract")
    payments = relationship(
        "Payment", back_populates="contract", cascade="all, delete-orphan"
    )


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
    actuaciones = relationship(
        "Actuacion", back_populates="court_case", cascade="all, delete-orphan"
    )
    agenda_events = relationship(
        "AgendaEvent", back_populates="court_case", cascade="all, delete-orphan"
    )


# --- Pagos ---
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)

    amount = Column(Numeric(10, 2), nullable=False)
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

    # Expediente manual (texto libre)
    expediente_manual = Column(String(100), nullable=True)

    court_case = relationship("CourtCase", back_populates="agenda_events")


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


# --- Respaldos del sistema ---
class Backup(Base):
    __tablename__ = "backups"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False)
    size = Column(BigInteger, default=0)
    sha256 = Column(String(64), nullable=True)
    num_records = Column(Integer, default=0)
    num_files = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    notes = Column(Text, nullable=True)


# --- Formatos de Demanda ---
class FormatoDemanda(Base):
    __tablename__ = "formatos_demanda"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False, index=True)

    titulo = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    resumen_ia = Column(Text, nullable=True)
    palabras_clave = Column(String(500), nullable=True)
    tipo_juicio = Column(String(100), nullable=True)
    contenido_texto = Column(Text, nullable=True)

    archivo_key = Column(String(500), nullable=False)
    archivo_nombre_original = Column(String(300), nullable=True)
    archivo_peso_bytes = Column(Integer, nullable=True)

    descargas = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    firm = relationship("Firm", backref="formatos_demanda")
