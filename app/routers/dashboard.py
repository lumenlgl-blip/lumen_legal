from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Payment, AgendaEvent, User
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