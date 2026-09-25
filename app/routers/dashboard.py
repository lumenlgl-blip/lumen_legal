from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from app.database import get_db
from app.models.core import (
    Client, Contract, CourtCase, Payment, AgendaEvent, User,
)
from datetime import datetime, date, timedelta
import calendar

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/", response_class=HTMLResponse)
async def show_dashboard(request: Request):
    with open("app/templates/dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@router.get("/stats")
async def get_stats(db: Session = Depends(get_db)):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    
    # Total de clientes
    total_clients = db.query(func.count(Client.id)).scalar() or 0
    
    # Juicios activos
    active_cases = db.query(func.count(CourtCase.id)).filter(
        CourtCase.status == "iniciado"
    ).scalar() or 0
    
    # Contratos pendientes de liquidar
    pending_contracts = db.query(func.count(Contract.id)).filter(
        Contract.status == "pendiente"
    ).scalar() or 0
    
    # Ingresos del mes actual
    current_month = today.month
    current_year = today.year
    month_start = date(current_year, current_month, 1)
    
    monthly_payments = db.query(func.sum(Payment.amount)).filter(
        Payment.payment_date >= month_start
    ).scalar() or 0
    
    # Deuda total pendiente
    contracts = db.query(Contract).filter(Contract.status == "pendiente").all()
    total_debt = 0
    for contract in contracts:
        paid = db.query(func.sum(Payment.amount)).filter(
            Payment.contract_id == contract.id
        ).scalar() or 0
        total_debt += float(contract.total_cost) - float(paid)
    
    # Eventos de hoy
    today_events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date == today,
        AgendaEvent.is_completed == False
    ).order_by(AgendaEvent.event_time).all()
    
    today_events_data = [{
        "id": e.id,
        "title": e.title,
        "event_type": e.event_type,
        "event_time": e.event_time or "",
        "location": e.location or ""
    } for e in today_events]
    
    # Eventos de mañana
    tomorrow_events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date == tomorrow,
        AgendaEvent.is_completed == False
    ).order_by(AgendaEvent.event_time).all()
    
    tomorrow_events_data = [{
        "id": e.id,
        "title": e.title,
        "event_type": e.event_type,
        "event_time": e.event_time or "",
        "location": e.location or ""
    } for e in tomorrow_events]
    
    # Ingresos por mes (últimos 6 meses)
    monthly_income = []
    for i in range(5, -1, -1):
        month = current_month - i
        year = current_year
        if month <= 0:
            month += 12
            year -= 1
        
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1)
        else:
            month_end = date(year, month + 1, 1)
        
        total = db.query(func.sum(Payment.amount)).filter(
            Payment.payment_date >= month_start,
            Payment.payment_date < month_end
        ).scalar() or 0
        
        monthly_income.append({
            "month": f"{month}/{year}",
            "total": float(total)
        })
    
    # Distribución de tipos de juicio
    case_types = db.query(CourtCase.status, func.count(CourtCase.id)).group_by(CourtCase.status).all()
    case_types_data = [{"status": s, "count": c} for s, c in case_types]
    
    # Clientes registrados este mes
    new_clients_month = db.query(func.count(Client.id)).filter(
        Client.created_at >= month_start
    ).scalar() or 0
    
    return {
        "total_clients": total_clients,
        "active_cases": active_cases,
        "pending_contracts": pending_contracts,
        "monthly_income": float(monthly_payments),
        "total_debt": float(total_debt),
        "new_clients_month": new_clients_month,
        "today_events": today_events_data,
        "tomorrow_events": tomorrow_events_data,
        "monthly_income_history": monthly_income,
        "case_types": case_types_data
    }
    
    # ============================================================
# MODAL 1: CLIENTES REGISTRADOS
# ============================================================
@router.get("/clients")
async def get_all_clients(db: Session = Depends(get_db)):
    """Lista completa de clientes con teléfono, # contratos y # juicios activos."""
    clients = db.query(Client).order_by(Client.id.desc()).all()
    result = []
    for c in clients:
        contracts = db.query(Contract).filter(Contract.client_id == c.id).all()
        total_contratos = len(contracts)

        # Juicios activos = CourtCase con status iniciado
        contract_ids = [ct.id for ct in contracts]
        juicios_activos = 0
        if contract_ids:
            juicios_activos = (
                db.query(CourtCase)
                .filter(
                    CourtCase.contract_id.in_(contract_ids),
                    CourtCase.status == "iniciado",
                )
                .count()
            )

        result.append({
            "id": c.id,
            "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}".strip(),
            "curp": c.curp,
            "telefono": c.phone,
            "email": c.email or "—",
            "folio_registro": c.folio_registro,
            "expediente_interno": c.expediente_interno,
            "total_contratos": total_contratos,
            "juicios_activos": juicios_activos,
            "created_at": c.created_at.strftime("%d/%m/%Y") if c.created_at else "—",
        })
    return result


# ============================================================
# MODAL 2: JUICIOS ACTIVOS
# ============================================================
@router.get("/active-cases")
async def get_active_cases(db: Session = Depends(get_db)):
    """Todos los juicios activos con expediente, partes, tipo y estatus."""
    cases = (
        db.query(CourtCase, Contract, Client)
        .join(Contract, CourtCase.contract_id == Contract.id)
        .join(Client, Contract.client_id == Client.id)
        .filter(CourtCase.status == "iniciado")
        .order_by(CourtCase.fecha_presentacion.desc())
        .all()
    )

    result = []
    for case, contract, client in cases:
        # Separar actores si es bilateral
        actor = case.actor_nombre or ""
        demandado = case.demandado_nombre or ""

        result.append({
            "id": case.id,
            "num_exp_tribunal": case.num_exp_tribunal,
            "folio_tribunal": case.folio_tribunal,
            "tribunal": case.tribunal,
            "secretaria": case.secretaria,
            "tipo_juicio": contract.tipo_juicio or contract.tipo_juicio_otro or "—",
            "actor_nombre": actor or "—",
            "demandado_nombre": demandado or "—",
            "status": case.status,
            "fecha_presentacion": case.fecha_presentacion.strftime("%d/%m/%Y")
            if case.fecha_presentacion else "—",
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}".strip(),
            "cliente_telefono": client.phone,
        })
    return result


# ============================================================
# MODAL 3: INGRESOS DEL MES
# ============================================================
@router.get("/monthly-income")
async def get_monthly_income(db: Session = Depends(get_db)):
    """Pagos registrados este mes con cliente, contrato y expediente."""
    today = date.today()
    month_start = date(today.year, today.month, 1)

    payments = (
        db.query(Payment, Contract, Client)
        .join(Contract, Payment.contract_id == Contract.id)
        .join(Client, Contract.client_id == Client.id)
        .filter(Payment.payment_date >= month_start)
        .order_by(Payment.payment_date.desc())
        .all()
    )

    result = []
    total = 0.0
    for payment, contract, client in payments:
        court_case = (
            db.query(CourtCase)
            .filter(CourtCase.contract_id == contract.id)
            .first()
        )

        monto = float(payment.amount)
        total += monto

        result.append({
            "id": payment.id,
            "fecha": payment.payment_date.strftime("%d/%m/%Y %H:%M")
            if payment.payment_date else "—",
            "monto": monto,
            "metodo": payment.method or "—",
            "recibio": payment.receiver_name or "—",
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}".strip(),
            "cliente_telefono": client.phone,
            "service_type": contract.service_type,
            "tipo_juicio": contract.tipo_juicio or contract.tipo_juicio_otro or "—",
            "num_exp_tribunal": court_case.num_exp_tribunal if court_case else "—",
            "tribunal": court_case.tribunal if court_case else "—",
        })

    return {"total": total, "pagos": result}


# ============================================================
# MODAL 4: DEUDA PENDIENTE
# ============================================================
@router.get("/debt")
async def get_debt(db: Session = Depends(get_db)):
    """Clientes con deuda: contrato, expediente, cobrado, pagado, debe, teléfono."""
    # Contratos pendientes con cliente
    rows = (
        db.query(Contract, Client)
        .join(Client, Contract.client_id == Client.id)
        .filter(Contract.status == "pendiente")
        .order_by(Client.id.desc())
        .all()
    )

    result = []
    total_deuda = 0.0
    for contract, client in rows:
        pagado = (
            db.query(func.sum(Payment.amount))
            .filter(Payment.contract_id == contract.id)
            .scalar() or 0
        )
        pagado = float(pagado)
        cobrado = float(contract.total_cost)
        debe = cobrado - pagado
        total_deuda += debe

        court_case = (
            db.query(CourtCase)
            .filter(CourtCase.contract_id == contract.id)
            .first()
        )

        result.append({
            "contract_id": contract.id,
            "cliente_id": client.id,
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}".strip(),
            "cliente_telefono": client.phone,
            "folio_registro": client.folio_registro,
            "expediente_interno": client.expediente_interno,
            "service_type": contract.service_type,
            "tipo_juicio": contract.tipo_juicio or contract.tipo_juicio_otro or "—",
            "specific_detail": contract.specific_detail or "—",
            "cobrado": cobrado,
            "pagado": pagado,
            "debe": debe,
            "num_exp_tribunal": court_case.num_exp_tribunal if court_case else "—",
            "tribunal": court_case.tribunal if court_case else "—",
            "case_status": court_case.status if court_case else "—",
            "contrato_created_at": contract.created_at.strftime("%d/%m/%Y")
            if contract.created_at else "—",
        })

    return {"total_deuda": total_deuda, "clientes": result}


# ============================================================
# MODAL 5: CONTRATOS PENDIENTES
# ============================================================
@router.get("/pending-contracts")
async def get_pending_contracts(db: Session = Depends(get_db)):
    """Contratos pendientes con nombre cliente, teléfono, tipo, cobrado, pagado, debe."""
    rows = (
        db.query(Contract, Client)
        .join(Client, Contract.client_id == Client.id)
        .filter(Contract.status == "pendiente")
        .order_by(Contract.created_at.desc())
        .all()
    )

    result = []
    for contract, client in rows:
        pagado = (
            db.query(func.sum(Payment.amount))
            .filter(Payment.contract_id == contract.id)
            .scalar() or 0
        )
        pagado = float(pagado)
        cobrado = float(contract.total_cost)
        debe = cobrado - pagado

        court_case = (
            db.query(CourtCase)
            .filter(CourtCase.contract_id == contract.id)
            .first()
        )

        result.append({
            "contract_id": contract.id,
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}".strip(),
            "cliente_telefono": client.phone,
            "folio_registro": client.folio_registro,
            "service_type": contract.service_type,
            "tipo_juicio": contract.tipo_juicio or contract.tipo_juicio_otro or "—",
            "specific_detail": contract.specific_detail or "—",
            "cobrado": cobrado,
            "pagado": pagado,
            "debe": debe,
            "num_exp_tribunal": court_case.num_exp_tribunal if court_case else "—",
            "tribunal": court_case.tribunal if court_case else "—",
            "fecha_contratacion": contract.created_at.strftime("%d/%m/%Y")
            if contract.created_at else "—",
        })

    return result