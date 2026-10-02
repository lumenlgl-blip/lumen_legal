# ============================================================
# Dockerfile para Lumen Legal — Render con LibreOffice
# ============================================================
# ============================================================
# Dockerfile para Lumen Legal — Render con LibreOffice + WeasyPrint
# ============================================================
FROM python:3.14-slim

# Evitar prompts interactivos durante apt-get
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Instalar LibreOffice + dependencias de WeasyPrint ────────
# LibreOffice: conversión DOCX → PDF para vista previa de formatos.
# Pango/Cairo/HarfBuzz: requeridos por WeasyPrint para generar
# constancias, recibos y reportes en PDF (no vienen en slim).
RUN apt-get update && apt-get install -y --no-install-recommends \
    # LibreOffice (conversión DOCX → PDF)
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-core \
    # Fuentes tipográficas
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig \
    # Certificados y utilidades
    ca-certificates \
    curl \
    # ── Dependencias de WeasyPrint (Pango / Cairo / HarfBuzz) ──
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    libjpeg62-turbo \
    libopenjp2-7 \
    libpng16-16 \
    shared-mime-info \
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
