# ============================================================
# Dockerfile para Lumen Legal — Render con LibreOffice
# ============================================================
FROM python:3.14-slim

# Evitar prompts interactivos durante apt-get
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Instalar LibreOffice y dependencias del sistema ──────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-core \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verificar que LibreOffice quedó instalado correctamente
RUN which libreoffice && libreoffice --version

# ── Directorio de trabajo ────────────────────────────────────
WORKDIR /app

# ── Instalar dependencias de Python ─────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copiar el resto del proyecto ─────────────────────────────
COPY . .

# ── Crear carpeta temporal para conversiones ─────────────────
RUN mkdir -p /app/storage/temp

# ── Exponer puerto (Render usa la variable PORT) ─────────────
EXPOSE 10000

# ── Comando de inicio ────────────────────────────────────────
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
