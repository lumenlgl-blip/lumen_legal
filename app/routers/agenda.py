from fastapi import APIRouter, Depends, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import AgendaEvent, CourtCase, Contract, Client, User, ActivityLog
from datetime import datetime, date, timedelta
import pytz
import os
import base64
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

# Zona horaria de México (CDMX)
MEXICO_TZ = pytz.timezone('America/Mexico_City')

def get_mexico_time():
    return datetime.now(MEXICO_TZ)

def get_today_date():
    return get_mexico_time().date()

router = APIRouter(prefix="/agenda", tags=["Agenda"])

# --- Ruta principal de agenda ---
@router.get("/", response_class=HTMLResponse)
async def show_agenda(request: Request):
    with open("app/templates/agenda.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- Obtener eventos del mes ---
@router.get("/events/{year}/{month}")
async def get_month_events(year: int, month: int, db: Session = Depends(get_db)):
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)
    
    events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date >= start_date,
        AgendaEvent.event_date < end_date
    ).order_by(AgendaEvent.event_date, AgendaEvent.event_time).all()
    
    events_data = []
    for event in events:
        events_data.append({
            "id": event.id,
            "title": event.title,
            "event_type": event.event_type,
            "description": event.description or "",
            "event_date": event.event_date.strftime("%Y-%m-%d"),
            "event_time": event.event_time or "",
            "location": event.location or "",
            "is_completed": event.is_completed,
            "expediente_manual": event.expediente_manual or ""
        })
    
    return events_data

# --- Obtener un evento específico por ID ---
@router.get("/event/{event_id}")
async def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(AgendaEvent).filter(AgendaEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Evento no encontrado")
    
    return {
        "id": event.id,
        "title": event.title,
        "event_type": event.event_type,
        "description": event.description or "",
        "event_date": event.event_date.strftime("%Y-%m-%d"),
        "event_time": event.event_time or "",
        "location": event.location or "",
        "is_completed": event.is_completed,
        "expediente_manual": event.expediente_manual or ""
    }

# --- Crear evento ---
@router.post("/create")
async def create_event(
    title: str = Form(...),
    event_type: str = Form(...),
    description: str = Form(None),
    event_date: str = Form(...),
    event_time: str = Form(None),
    location: str = Form(None),
    expediente_manual: str = Form(None),
    db: Session = Depends(get_db)
):
    new_event = AgendaEvent(
        firm_id=1,
        user_id=1,
        title=title,
        event_type=event_type,
        description=description,
        event_date=datetime.strptime(event_date, "%Y-%m-%d").date(),
        event_time=event_time,
        location=location,
        expediente_manual=expediente_manual
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    
    # Registrar en bitácora
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="create",
        entity="Agenda",
        entity_id=new_event.id,
        description=f"Creó evento: {title}"
    )
    db.add(log)
    db.commit()
    
    return {"message": "Evento creado exitosamente", "id": new_event.id}

# --- Actualizar evento ---
@router.put("/update/{event_id}")
async def update_event(
    event_id: int,
    title: str = Form(...),
    event_type: str = Form(...),
    description: str = Form(None),
    event_date: str = Form(...),
    event_time: str = Form(None),
    location: str = Form(None),
    expediente_manual: str = Form(None),
    db: Session = Depends(get_db)
):
    event = db.query(AgendaEvent).filter(AgendaEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Evento no encontrado")
    
    event.title = title
    event.event_type = event_type
    event.description = description
    event.event_date = datetime.strptime(event_date, "%Y-%m-%d").date()
    event.event_time = event_time
    event.location = location
    event.expediente_manual = expediente_manual
    
    db.commit()
    db.refresh(event)
    
    # Registrar en bitácora
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="update",
        entity="Agenda",
        entity_id=event.id,
        description=f"Actualizó evento: {title}"
    )
    db.add(log)
    db.commit()
    
    return {"message": "Evento actualizado exitosamente", "id": event.id}

# --- Marcar completado ---
@router.put("/complete/{event_id}")
async def complete_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(AgendaEvent).filter(AgendaEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Evento no encontrado")
    event.is_completed = not event.is_completed
    db.commit()
    
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="update",
        entity="Agenda",
        entity_id=event.id,
        description=f"Cambió estado del evento: {event.title}"
    )
    db.add(log)
    db.commit()
    
    return {"message": "Estado actualizado", "is_completed": event.is_completed}

# --- Eliminar evento ---
@router.delete("/delete/{event_id}")
async def delete_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(AgendaEvent).filter(AgendaEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Evento no encontrado")
    
    event_title = event.title
    
    db.delete(event)
    db.commit()
    
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="delete",
        entity="Agenda",
        entity_id=event_id,
        description=f"Eliminó evento: {event_title}"
    )
    db.add(log)
    db.commit()
    
    return {"message": "Evento eliminado"}

# --- Buscar expedientes para vincular (ya no se usa, pero se mantiene para compatibilidad) ---
@router.post("/search-case")
async def search_case(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    cases = db.query(CourtCase).filter(
        CourtCase.num_exp_tribunal.ilike(f"%{search_term}%")
    ).all()
    
    return [{"id": c.id, "expediente": c.num_exp_tribunal, "tribunal": c.tribunal} for c in cases]

# --- Obtener notificaciones de eventos próximos ---
@router.get("/notifications")
async def get_notifications(db: Session = Depends(get_db)):
    today = get_today_date()
    tomorrow = today + timedelta(days=1)
    in_3_days = today + timedelta(days=3)
    in_7_days = today + timedelta(days=7)
    
    today_events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date == today,
        AgendaEvent.is_completed == False
    ).all()
    
    tomorrow_events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date == tomorrow,
        AgendaEvent.is_completed == False
    ).all()
    
    upcoming_events = db.query(AgendaEvent).filter(
        AgendaEvent.event_date > today,
        AgendaEvent.event_date <= in_7_days,
        AgendaEvent.is_completed == False
    ).order_by(AgendaEvent.event_date, AgendaEvent.event_time).all()
    
    legal_deadlines = db.query(AgendaEvent).filter(
        AgendaEvent.event_type == "plazos",
        AgendaEvent.event_date >= today,
        AgendaEvent.event_date <= in_3_days,
        AgendaEvent.is_completed == False
    ).order_by(AgendaEvent.event_date).all()
    
    return {
        "today": [{"id": e.id, "title": e.title, "time": e.event_time or "", "type": e.event_type} for e in today_events],
        "tomorrow": [{"id": e.id, "title": e.title, "time": e.event_time or "", "type": e.event_type} for e in tomorrow_events],
        "upcoming": [{"id": e.id, "title": e.title, "date": e.event_date.strftime("%d/%m/%Y"), "time": e.event_time or "", "type": e.event_type} for e in upcoming_events],
        "deadlines": [{"id": e.id, "title": e.title, "date": e.event_date.strftime("%d/%m/%Y")} for e in legal_deadlines],
        "total_pending": len(today_events) + len(tomorrow_events) + len(upcoming_events)
    }

# --- Generar reporte PDF de agenda ---
@router.get("/report-pdf")
async def generate_agenda_report(
    periodo: str = "mes",
    filtro: str = "pendientes",
    db: Session = Depends(get_db)
):
    today = get_today_date()
    
    if periodo == "mes":
        start_date = date(today.year, today.month, 1)
        if today.month == 12:
            end_date = date(today.year + 1, 1, 1)
        else:
            end_date = date(today.year, today.month + 1, 1)
        titulo_periodo = f"Mes de {today.strftime('%B %Y')}"
    elif periodo == "semana":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        titulo_periodo = f"Semana del {start_date.strftime('%d/%m/%Y')} al {(end_date - timedelta(days=1)).strftime('%d/%m/%Y')}"
    else:
        start_date = today
        end_date = today + timedelta(days=1)
        titulo_periodo = f"Día {today.strftime('%d/%m/%Y')}"
    
    query = db.query(AgendaEvent).filter(
        AgendaEvent.event_date >= start_date,
        AgendaEvent.event_date < end_date
    )
    
    if filtro == "pendientes":
        query = query.filter(AgendaEvent.is_completed == False)
    
    events = query.order_by(AgendaEvent.event_date, AgendaEvent.event_time).all()
    
    events_data = []
    for event in events:
        events_data.append({
            "fecha": event.event_date.strftime('%d/%m/%Y'),
            "hora": event.event_time or "Todo el día",
            "titulo": event.title,
            "tipo": event.event_type.upper(),
            "lugar": event.location or "",
            "descripcion": event.description or "",
            "expediente": event.expediente_manual or "",
            "estado": "✅" if event.is_completed else "⏳"
        })
    
    # Convertir logo a base64
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode('utf-8')
    
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/reporte_agenda.html")
    
    html_content = template.render(
        logo_base64=logo_base64,
        titulo_periodo=titulo_periodo,
        total_eventos=len(events_data),
        eventos=events_data,
        anio=datetime.now().strftime('%Y'),
        fecha_generacion=datetime.now().strftime('%d/%m/%Y %H:%M')
    )
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=reporte_agenda_{periodo}.pdf"}
    )