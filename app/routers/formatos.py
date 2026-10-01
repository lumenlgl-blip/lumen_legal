from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from html import escape
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import FormatoDemanda, Client, ActivityLog
from app.routers.auth import get_current_user
from app.storage import upload_fileobj, delete_file, get_file_url, s3_client, R2_BUCKET
from app.services import converter
import uuid, io, json
from datetime import datetime
from typing import Optional
import os
import requests
from docx import Document

router = APIRouter(prefix="/formatos", tags=["Formatos de Demanda"])

# ── Configuración Groq (gratuita, sin tarjeta) ────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"


# ============================================================
# HELPERS
# ============================================================
def extraer_texto_docx(file_bytes: bytes, max_chars: int = 12000) -> str:
    """
    Extrae el texto de un .docx.
      1) Intenta con python-docx (método oficial).
      2) Si falla, hace fallback leyendo el XML directamente del ZIP.
      3) Si todo falla, devuelve "" sin romper el flujo.
    """
    # ── INTENTO 1: python-docx ────────────────────────────────
    try:
        doc = Document(io.BytesIO(file_bytes))
        partes = [p.text for p in doc.paragraphs if p.text.strip()]
        for tabla in doc.tables:
            for fila in tabla.rows:
                for celda in fila.cells:
                    if celda.text.strip():
                        partes.append(celda.text.strip())
        texto = "\n".join(partes)
        if texto.strip():
            return texto[:max_chars]
    except Exception as e:
        print(f"⚠️ python-docx falló, probando fallback ZIP: {e}")

    # ── INTENTO 2 (FALLBACK): leer XML directo del ZIP ────────
    try:
        import zipfile
        import re

        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            # Buscar word/document.xml (principal)
            candidatos = [
                n
                for n in z.namelist()
                if n == "word/document.xml" or n.endswith("/document.xml")
            ]
            # Si no, cualquier XML dentro de word/
            if not candidatos:
                candidatos = [
                    n
                    for n in z.namelist()
                    if n.startswith("word/") and n.endswith(".xml")
                ]

            for name in candidatos:
                try:
                    xml_content = z.read(name).decode("utf-8", errors="ignore")

                    # Extraer texto entre <w:t ...>...</w:t>
                    textos = re.findall(
                        r"<w:t[^>]*>(.*?)</w:t>", xml_content, re.DOTALL
                    )
                    if not textos:
                        continue

                    texto = "\n".join(textos)

                    # Decodificar entidades XML básicas
                    texto = (
                        texto.replace("&amp;", "&")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&quot;", '"')
                        .replace("&apos;", "'")
                        .replace("&#10;", "\n")
                        .replace("&#13;", "\n")
                        .replace("&#9;", "\t")
                    )

                    # Colapsar saltos múltiples
                    texto = re.sub(r"\n{2,}", "\n", texto).strip()

                    if texto:
                        print(f"✅ Extracción por fallback ZIP OK ({len(texto)} chars)")
                        return texto[:max_chars]
                except Exception as inner_e:
                    print(f"⚠️ Fallback falló en {name}: {inner_e}")
                    continue
    except Exception as e:
        print(f"⚠️ Fallback ZIP también falló: {e}")

    # ── Si todo falló ─────────────────────────────────────────
    print(
        "❌ No se pudo extraer texto del documento (formato no soportado o corrupto)."
    )
    return ""


def generar_resumen_ia(texto: str) -> dict:
    """Genera una síntesis jurídica estructurada del formato.

    La síntesis es ADAPTATIVA: identifica el tipo de escrito y resume
    únicamente lo que consta, sin inventar información.
    """

    if not GROQ_API_KEY or not texto.strip():
        return {
            "resumen": texto[:1200] if texto else "",
            "palabras_clave": "",
            "tipo_juicio": "",
            "ok": False,
        }

    # Límite conservador por el TPM free tier de Groq (8000).
    texto_analisis = texto[:14000]

    system_prompt = """Eres un abogado mexicano especializado en análisis y
clasificación de escritos judiciales mexicanos.

Tu tarea es analizar un formato documental existente y generar una síntesis
jurídica estructurada que permita identificarlo y recuperarlo después mediante
búsquedas en lenguaje natural.

Reglas absolutas:

- NO redactes una demanda nueva.
- NO inventes hechos, nombres, fechas, montos, autoridades ni cláusulas.
- NO incluyas artículos, fundamentos legales, tesis ni jurisprudencia.
- Describe únicamente lo que consta en el documento.
- Si un apartado no aparece en el documento, omítelo por completo.
- Responde exclusivamente con JSON válido, sin texto adicional."""

    prompt = f"""Analiza el siguiente FORMATO DE ESCRITO JUDICIAL y devuélveme una
síntesis estructurada.

INSTRUCCIONES:

1. Identifica primero qué clase de documento es realmente (demanda,
   contestación, promoción, incidente, recurso, amparo, sucesorio, alimentos,
   guarda y custodia, convenio, ejecución, solicitud u otro). No presupongas
   que es divorcio.

2. Redacta la síntesis usando ÚNICAMENTE los apartados que apliquen al
   documento, en este orden:

Tipo de documento
Procedimiento o materia
Partes y sujetos relevantes
Objeto y finalidad
Hechos o antecedentes relevantes
Pretensiones o peticiones
Medidas provisionales o precautorias
Aspectos familiares o patrimoniales
Propuesta de convenio o convenio
Pruebas y anexos

3. Formato de presentación:
- Cada apartado va en su propio bloque, con título en texto normal seguido
  de la información correspondiente.
- Cuando haya varios elementos, usa viñetas simples con guion (-).
- NO uses Markdown, ni asteriscos, ni negritas, ni símbolos decorativos.
- Evita párrafos largos; sé puntual y concreto.
- No repitas información entre apartados.

4. En "Partes y sujetos relevantes" incluye solo a quienes son jurídicamente
   relevantes para comprender el asunto (promovente, demandado, cónyuge,
   hijos, causante, herederos, acreedores, etc.). NO incluyas abogados,
   procuradores, domicilios, teléfonos, correos ni datos del despacho.

5. En "Propuesta de convenio o convenio" incluye el apartado únicamente si
   el documento contiene un convenio o propuesta. Resume las materias:
   guarda y custodia, convivencia, alimentos, domicilio de hijos,
   administración de bienes, liquidación patrimonial, compensación u otras
   cláusulas que consten.

EJEMPLO DEL FORMATO ESPERADO (referencia de estilo):

Tipo de documento
Demanda de divorcio con propuesta de convenio.

Procedimiento o materia
Divorcio judicial.

Partes y sujetos relevantes
- Cónyuge promovente.
- Cónyuge demandado.
- Hijos, cuando sean relevantes para las prestaciones solicitadas.

Objeto y finalidad
Solicitar la disolución del vínculo matrimonial y establecer las
consecuencias derivadas de ella.

Hechos o antecedentes relevantes
- Matrimonio entre las partes.
- Existencia de hijos.
- Separación de las partes.

Pretensiones o peticiones
- Disolución del vínculo matrimonial.
- Las demás prestaciones expresamente solicitadas.

Propuesta de convenio o convenio
- Guarda y custodia.
- Régimen de convivencia.
- Alimentos.
- Domicilio de los hijos.
- Aspectos patrimoniales, cuando correspondan.

Pruebas y anexos
- Documentos y pruebas expresamente señalados en el formato.

FIN DEL EJEMPLO.

DEVUELVE ÚNICAMENTE ESTE JSON (sin texto fuera del JSON):

{{
  "resumen": "Síntesis organizada por apartados, sin Markdown ni asteriscos.",
  "palabras_clave": "entre 8 y 20 términos o expresiones jurídicas separadas por comas",
  "tipo_juicio": "nombre corto y preciso del procedimiento o clase de escrito"
}}

TEXTO DEL FORMATO:

<<<INICIO>>>
{texto_analisis}
<<<FIN>>>
"""

    try:
        data = llamar_groq_json(
            system_prompt,
            prompt,
            max_tokens=3500,
            temperature=0.05,
        )

        if not isinstance(data, dict):
            raise ValueError("Groq no devolvió un objeto JSON")

        resumen = str(data.get("resumen") or "").strip()
        palabras = str(data.get("palabras_clave") or "").strip()
        tipo = str(data.get("tipo_juicio") or "").strip()

        if not resumen:
            resumen = texto[:1200].strip()
            print("⚠️ Groq devolvió síntesis vacía, usando texto original.")

        ok = bool(resumen) and len(resumen) > 150

        print(
            f"📝 Síntesis: {len(resumen)} chars | "
            f"keywords: {palabras[:120]} | tipo: {tipo}"
        )

        return {
            "resumen": resumen,
            "palabras_clave": palabras,
            "tipo_juicio": tipo,
            "ok": ok,
        }

    except Exception as e:
        print(f"⚠️ Error Groq en síntesis: {e}")

        return {
            "resumen": texto[:1200] if texto else "",
            "palabras_clave": "",
            "tipo_juicio": "",
            "ok": False,
        }


# ============================================================
# VISTA PRINCIPAL
# ============================================================
@router.get("", response_class=HTMLResponse)
async def show_formatos_page():
    with open("app/templates/formatos_demanda.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# LISTAR / BUSCAR FORMATOS
# ============================================================
@router.get("/list")
async def list_formatos(q: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(FormatoDemanda)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                FormatoDemanda.titulo.ilike(term),
                FormatoDemanda.descripcion.ilike(term),
                FormatoDemanda.resumen_ia.ilike(term),
                FormatoDemanda.palabras_clave.ilike(term),
                FormatoDemanda.tipo_juicio.ilike(term),
            )
        )
    formatos = query.order_by(FormatoDemanda.updated_at.desc()).all()
    return [
        {
            "id": f.id,
            "titulo": f.titulo,
            "descripcion": f.descripcion or "",
            "resumen_ia": f.resumen_ia or "",
            "palabras_clave": f.palabras_clave or "",
            "tipo_juicio": f.tipo_juicio or "",
            "archivo_nombre_original": f.archivo_nombre_original or "",
            "descargas": f.descargas or 0,
            "created_at": f.created_at.strftime("%d/%m/%Y") if f.created_at else "",
            "updated_at": (
                f.updated_at.strftime("%d/%m/%Y %H:%M") if f.updated_at else ""
            ),
        }
        for f in formatos
    ]


# ============================================================
# BÚSQUEDA IA AVANZADA — Groq lee el contenido de TODOS los formatos
# ============================================================

# ============================================================
# MOTOR DE BÚSQUEDA IA — HELPERS
# ============================================================
import math
import re
import time
import unicodedata
from collections import Counter, OrderedDict

# ── Caché LRU en memoria para smart-search ──
_SMART_CACHE = OrderedDict()
_SMART_CACHE_MAX = 100
_SMART_CACHE_TTL = 600  # 10 minutos

SEARCH_STOPWORDS = {
    "a",
    "al",
    "algo",
    "alguna",
    "alguno",
    "algunos",
    "ante",
    "con",
    "como",
    "contra",
    "cual",
    "cuando",
    "de",
    "del",
    "desde",
    "donde",
    "el",
    "ella",
    "ellas",
    "ellos",
    "en",
    "entre",
    "es",
    "esa",
    "ese",
    "eso",
    "esta",
    "este",
    "esto",
    "estos",
    "ha",
    "hacia",
    "hay",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "mis",
    "muy",
    "no",
    "o",
    "para",
    "por",
    "que",
    "qué",
    "se",
    "sea",
    "si",
    "sin",
    "sobre",
    "son",
    "su",
    "sus",
    "tambien",
    "también",
    "un",
    "una",
    "unas",
    "uno",
    "unos",
    "y",
    "ya",
    "yo",
}


def normalizar_busqueda(texto: str) -> str:
    texto = texto or ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9áéíóúüñ\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def tokens_busqueda(texto: str):
    texto = normalizar_busqueda(texto)
    return [t for t in re.findall(r"[a-z0-9ñ]{2,}", texto) if t not in SEARCH_STOPWORDS]


def expandir_token(token: str):
    """Expansión ligera y normalizada para español jurídico.

    Groq hace la expansión semántica principal; este diccionario sirve como
    segunda capa local para que la recuperación siga funcionando si la IA falla.
    """
    grupos = [
        {"custodia", "guarda", "cuidado", "guarda custodia"},
        {"convivencia", "visitas", "contacto", "regimen de convivencia"},
        {"alimentos", "pension", "alimentaria", "manutencion", "pension alimenticia"},
        {"divorcio", "disolucion", "matrimonial"},
        {"sucesorio", "herencia", "hereditario", "testamentario", "intestamentario"},
        {"bienes", "patrimonio", "liquidacion", "gananciales", "sociedad conyugal"},
        {
            "precautoria",
            "precautorio",
            "provisional",
            "urgente",
            "medida cautelar",
            "medida",
        },
        {"notificacion", "emplazamiento", "notificar", "emplazar"},
        {"audiencia", "comparecencia", "vista"},
        {"amparo", "constitucional", "suspension", "acto reclamado"},
        {"contestacion", "contestar", "demandado", "oposicion"},
        {"incidente", "incidental"},
        {"recurso", "apelacion", "revocacion", "impugnacion"},
        {"prueba", "pruebas", "documental", "testimonial", "pericial"},
        {"paternidad", "filiacion", "reconocimiento"},
        {"violencia", "violencia familiar", "orden proteccion", "proteccion"},
    ]
    token = normalizar_busqueda(token)
    for valores in grupos:
        if token in valores:
            return valores
    return {token}


def campo_busqueda(f):
    return {
        "titulo": normalizar_busqueda(f.titulo or ""),
        "tipo": normalizar_busqueda(f.tipo_juicio or ""),
        "keywords": normalizar_busqueda(f.palabras_clave or ""),
        "resumen": normalizar_busqueda(f.resumen_ia or ""),
        "descripcion": normalizar_busqueda(f.descripcion or ""),
        "contenido": normalizar_busqueda(f.contenido_texto or ""),
    }


def score_lexico_inteligente(f, consulta, perfil=None):
    """
    Recuperación local sobre TODO el contenido disponible.
    No depende de que el documento esté entre los más recientes.
    """
    campos = campo_busqueda(f)
    q_tokens = set(tokens_busqueda(consulta))

    if perfil:
        extra = []
        for key in ("conceptos", "sinonimos", "requisitos"):
            vals = perfil.get(key) or []
            if isinstance(vals, list):
                extra.extend(str(x) for x in vals)
        q_tokens.update(tokens_busqueda(" ".join(extra)))

    if not q_tokens:
        return 0.0, []

    # Pesos: el resumen IA está estructurado (Tipo, Procedimiento, Partes,
    # Objeto, Hechos, Pretensiones...) y por eso es la segunda señal más
    # discriminante después del título/tipo.
    pesos = {
        "titulo": 5.0,
        "tipo": 5.0,
        "resumen": 4.5,  # ↑ antes 3.0 — el resumen IA es clave
        "keywords": 4.0,
        "descripcion": 2.0,
        "contenido": 1.0,
    }

    score = 0.0
    evidencias = []

    for token in q_tokens:
        variantes = expandir_token(token)
        best = 0.0
        best_field = None

        for variante in variantes:
            for campo, peso in pesos.items():
                valor = campos[campo]
                if not valor:
                    continue

                # Coincidencia de palabra, no solo substring.
                hits = len(re.findall(r"\b" + re.escape(variante) + r"\b", valor))
                if hits:
                    # Rendimiento decreciente para no premiar documentos
                    # que repiten una palabra cientos de veces.
                    contrib = peso * min(1.0 + math.log1p(hits), 3.0)
                    if contrib > best:
                        best = contrib
                        best_field = campo

        if best:
            score += best
            if len(evidencias) < 8:
                evidencias.append(f"{token} → {best_field}")
    # Frases completas son una señal fuerte.
    q_norm = normalizar_busqueda(consulta)
    for frase in re.findall(r'"([^"]{3,})"', consulta):
        frase_n = normalizar_busqueda(frase)
        if frase_n and any(frase_n in campos[c] for c in campos):
            score += 12.0
            evidencias.append(f'frase exacta: "{frase}"')

    # ── BONUS por coherencia interna ─────────────────────────
    # Si los mismos términos aparecen en varios campos (título, tipo,
    # resumen, keywords), el formato es mucho más discriminante que si solo
    # aparecen en el cuerpo. Aplicamos un multiplicador suave.
    campos_con_hits = 0
    for campo in ("titulo", "tipo", "resumen", "keywords"):
        valor = campos[campo]
        if not valor:
            continue
        for token in q_tokens:
            variantes = expandir_token(token)
            if any(re.search(r"\b" + re.escape(v) + r"\b", valor) for v in variantes):
                campos_con_hits += 1
                break

    if campos_con_hits >= 3:
        score *= 1.20
        evidencias.append(f"coherencia: {campos_con_hits} campos coinciden")
    elif campos_con_hits == 2:
        score *= 1.10

    return score, evidencias


def extraer_snippets_relevantes(
    texto: str, consulta: str, perfil=None, max_snippets=5, window=650, max_chars=4200
):
    """
    En lugar de enviar solo los primeros caracteres del escrito,
    localiza zonas del documento relacionadas con la consulta.
    """
    if not texto:
        return ""

    original = texto
    norm = normalizar_busqueda(texto)
    terms = set(tokens_busqueda(consulta))

    if perfil:
        for key in ("conceptos", "sinonimos", "requisitos"):
            vals = perfil.get(key) or []
            if isinstance(vals, list):
                terms.update(tokens_busqueda(" ".join(str(x) for x in vals)))

    # Buscar posiciones sobre el texto normalizado; la longitud suele coincidir
    # con la original salvo caracteres acentuados/puntuación.
    posiciones = []
    for term in terms:
        for m in re.finditer(r"\b" + re.escape(term) + r"\b", norm):
            posiciones.append(m.start())

    if not posiciones:
        return original[:max_chars]

    posiciones = sorted(set(posiciones))
    ventanas = []
    for pos in posiciones:
        a = max(0, pos - window)
        b = min(len(original), pos + window)
        ventanas.append((a, b))

    # Fusionar ventanas cercanas.
    fusionadas = []
    for a, b in ventanas:
        if fusionadas and a <= fusionadas[-1][1] + 120:
            fusionadas[-1] = (fusionadas[-1][0], max(fusionadas[-1][1], b))
        else:
            fusionadas.append((a, b))

    # Priorizar las ventanas que tengan más términos de búsqueda.
    scored = []
    for a, b in fusionadas:
        fragmento = normalizar_busqueda(original[a:b])
        hits = sum(
            1 for t in terms if re.search(r"\b" + re.escape(t) + r"\b", fragmento)
        )
        scored.append((hits, a, b))

    scored.sort(reverse=True)

    partes = []
    usados = 0
    for _, a, b in scored[:max_snippets]:
        partes.append(original[a:b].strip())
        usados += len(partes[-1])
        if usados >= max_chars:
            break

    return "\n\n--- EVIDENCIA SEPARADA ---\n\n".join(partes)[:max_chars]


def llamar_groq_json(
    system_prompt: str, user_prompt: str, max_tokens=3000, temperature=0.05
):
    """Llama a Groq y devuelve JSON.

    Nunca imprime la API key. En errores HTTP muestra únicamente código y una
    parte segura del cuerpo de respuesta para facilitar diagnóstico.
    """
    if not GROQ_API_KEY:
        print("⚠️ Groq no configurado: GROQ_API_KEY está vacío.")
        return None

    # ── Truncado defensivo: 8000 TPM free tier ──
    # Groq cuenta (input + max_tokens de salida) contra el límite.
    PRESUPUESTO = 7500  # margen de 500 tokens
    chars_disponibles = int((PRESUPUESTO - max_tokens) * 3.2) - len(system_prompt)
    if chars_disponibles < 500:
        print(
            f"❌ Presupuesto insuficiente: max_tokens={max_tokens} deja {chars_disponibles} chars. Abortando."
        )
        return None
    if len(user_prompt) > chars_disponibles:
        print(f"⚠️ Truncando prompt: {len(user_prompt)} → {chars_disponibles} chars")
        user_prompt = user_prompt[:chars_disponibles]

    try:
        r = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "low",
            },
            timeout=25,
        )

        if not r.ok:
            cuerpo = (r.text or "").replace(GROQ_API_KEY, "[REDACTED]")
            print(f"❌ Groq HTTP {r.status_code}: {cuerpo[:1000]}")
            r.raise_for_status()

        payload = r.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("Groq no devolvió choices")

        contenido = choices[0].get("message", {}).get("content")
        if not contenido:
            raise ValueError("Groq devolvió contenido vacío")

        return json.loads(contenido)

    except requests.Timeout:
        print("❌ Groq agotó el tiempo de espera.")
        raise
    except requests.RequestException:
        raise
    except json.JSONDecodeError as e:
        print(f"❌ Groq devolvió JSON inválido: {e}")
        raise


def analizar_consulta_ia(consulta: str):
    """Convierte lenguaje natural en una ficha de recuperación documental."""
    fallback = {
        "intencion": consulta,
        "tipo_juicio": "",
        "conceptos": tokens_busqueda(consulta),
        "sinonimos": [],
        "requisitos": [],
        "exclusiones": [],
        "hechos": [],
        "prioridades": [],
    }

    system = (
        "Eres un analista jurídico mexicano especializado en recuperación "
        "documental. Convierte consultas de abogados en criterios de búsqueda. "
        "No redactes escritos y no inventes hechos. Devuelve exclusivamente JSON."
    )

    prompt = f"""CONSULTA DEL ABOGADO:
{consulta}

Tu tarea es detectar TODOS los elementos que permitan distinguir un formato útil
frente a otros formatos parecidos.

Devuelve exactamente:
{{
  "intencion": "qué documento o solución documental busca",
  "tipo_juicio": "procedimiento o clase de escrito si puede determinarse",
  "conceptos": ["conceptos jurídicos y fácticos esenciales"],
  "sinonimos": ["equivalentes jurídicos y expresiones naturales"],
  "requisitos": ["condiciones que el formato debería contener"],
  "exclusiones": ["condiciones que contradicen la consulta"],
  "hechos": ["hechos que cambian la utilidad del formato"],
  "prioridades": ["criterios ordenados del más discriminante al menos discriminante"]
}}

REGLAS:
- Detecta expresamente negaciones y contrastes: sin/con hijos, con/sin bienes,
  unilateral/bilateral, con/sin alimentos, con/sin medida precautoria, etc.
- Distingue materia de etapa procesal.
- Distingue tipo de procedimiento de finalidad del escrito.
- Detecta cuando el usuario busca un formato que contenga una estructura concreta,
  no necesariamente el mismo nombre del procedimiento.
- Incluye sinónimos en español jurídico mexicano y lenguaje cotidiano del abogado.
- No conviertas una posibilidad en requisito.
- No inventes artículos ni hechos.
- Devuelve ÚNICAMENTE el objeto JSON, sin texto antes ni después, sin comentarios y sin bloques de código markdown.
- Los valores de los arrays deben ser cadenas cortas (máx 60 caracteres cada una).
- No incluyas más de 8 elementos por array.
"""

    try:
        data = llamar_groq_json(system, prompt, max_tokens=2500, temperature=0.1)
        if isinstance(data, dict):
            for k in fallback:
                if k not in data or data[k] is None:
                    data[k] = fallback[k]
            return data
    except Exception as e:
        print(f"⚠️ analizar_consulta_ia: {e}")

    return fallback


def construir_prompt_reranking(consulta, candidatos):
    return f"""CONSULTA DEL ABOGADO:
{consulta}

TAREA: evaluar qué formatos de la biblioteca sirven realmente para esta consulta.

FUENTES DE EVIDENCIA DE CADA CANDIDATO:
- "resumen_estructurado": SÍNTESIS JURÍDICA DEL FORMATO YA GENERADA POR IA.
  Viene dividida en apartados (Tipo de documento, Procedimiento, Partes,
  Objeto, Hechos, Pretensiones, Convenio, Pruebas, etc.). ES LA FUENTE
  PRINCIPAL para juzgar el fondo del formato.
- "fragmentos_relevantes": extractos del texto original relacionados con la
  consulta. Úsalos para confirmar o refinar lo que dice el resumen.
- "keywords": términos jurídicos clave del formato.
- "titulo", "tipo_juicio", "descripcion": metadatos del registro.

CRITERIOS DE COMPARACIÓN (aplícalos todos):
1. Tipo de escrito o procedimiento.
2. Materia: familiar, civil, mercantil, penal, laboral, amparo...
3. Etapa procesal: demanda inicial, contestación, incidente, recurso...
4. Configuración fáctica: hijos sí/no, bienes sí/no, matrimonio/concubinato,
   alimentos sí/no, medidas precautorias sí/no, unilateral/bilateral.
5. Pretensiones concretas solicitadas.
6. Estructura reutilizable: sirve tal cual o requiere ajustes.

REGLAS:
- Analiza PRIMERO el "resumen_estructurado" de cada candidato.
- Confirma después con "fragmentos_relevantes" cuando sea necesario.
- Similitud de palabras NO basta. Analiza fondo jurídico.
- Contradicción con requisito esencial = penalización fuerte.
- Ausencia de un dato NO es contradicción.
- No supongas contenido no evidenciado.
- Sé estricto: si no encaja bien, baja el score.

ESCALA:
0-20 incompatible | 21-39 débil | 40-59 ajustes importantes
60-79 compatible | 80-94 muy compatible | 95-100 coincidente

REGLAS DE REDACCIÓN PARA CADA CAMPO:
- "razon": UNA sola frase (máx 140 caracteres) que resuma por qué sirve o no
  sirve. Empieza con el verbo: "Sirve porque...", "Coincide en...",
  "Requiere ajustes porque...", "No sirve porque...". Sin relleno, directo.
- "cumple": lista de 2 a 4 puntos concretos que SÍ coinciden con la consulta.
  Cada punto debe ser específico (ej: "Divorcio unilateral", "Sin hijos",
  "Incluye medida precautoria"), NO genérico (evita "Es una demanda").
- "faltantes": lista de 0 a 3 puntos concretos que faltan o hay que ajustar.
  Si no falta nada importante, deja la lista vacía [].
- "conflictos": lista de 0 a 3 contradicciones claras con la consulta.
  Solo cuando el formato dice algo que contradice expresamente lo pedido.
  Si no hay, deja la lista vacía [].
- "evidencia": 1 a 3 citas textuales CORTAS (máx 100 caracteres cada una)
  tomadas del resumen_estructurado o fragmentos_relevantes que respalden
  la valoración. Entre comillas.

DEVUELVE SOLO JSON:
{{
  "analisis": "una sola frase resumen de qué tan bien responde la biblioteca",
  "mejores": [
    {{
      "id": 123,
      "score": 0,
      "compatibilidad": "alto|medio|bajo|incompatible",
      "sirve_tal_cual": false,
      "razon": "frase directa de máx 140 caracteres",
      "cumple": ["punto específico 1", "punto específico 2"],
      "faltantes": ["ajuste necesario 1"],
      "conflictos": ["contradicción 1"],
      "evidencia": ["cita textual corta 1", "cita textual corta 2"]
    }}
  ]
}}

Ordena por compatibilidad. Solo IDs existentes. No fuerces un único ganador.

CANDIDATOS:
{json.dumps(candidatos, ensure_ascii=False, indent=2)}
"""


# ============================================================
# BÚSQUEDA IA AVANZADA — recuperación + reranking + verificación
# ============================================================
@router.post("/smart-search")
async def smart_search(
    request: Request,
    consulta: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    consulta = (consulta or "").strip()
    if not consulta:
        raise HTTPException(400, "Consulta vacía")
    if len(consulta) > 800:
        consulta = consulta[:800]

    # Caché: misma consulta + mismo firm_id → respuesta instantánea
    cache_key = (user.firm_id, consulta.lower())
    ahora = time.time()
    if cache_key in _SMART_CACHE:
        ts, cached = _SMART_CACHE[cache_key]
        if ahora - ts < _SMART_CACHE_TTL:
            _SMART_CACHE.move_to_end(cache_key)
            return cached
        else:
            del _SMART_CACHE[cache_key]

    formatos = (
        db.query(FormatoDemanda)
        .filter(FormatoDemanda.firm_id == user.firm_id)
        .order_by(FormatoDemanda.updated_at.desc())
        .all()
    )

    if not formatos:
        return {
            "analisis": "No hay formatos registrados todavía.",
            "total_analizados": 0,
            "candidatos_recuperados": 0,
            "resultados": [],
        }

    # 1) Recuperación léxica local (sin IA)
    recuperados = []
    for f in formatos:
        score_lex, evidencias = score_lexico_inteligente(f, consulta, None)
        if score_lex > 0:
            recuperados.append(
                {
                    "f": f,
                    "score_lex": score_lex,
                    "evidencias": evidencias,
                }
            )

    recuperados.sort(key=lambda x: x["score_lex"], reverse=True)

    # Filtro por umbral relativo: descartar candidatos muy débiles
    if len(recuperados) > 4:
        umbral = recuperados[0]["score_lex"] * 0.15
        recuperados = [r for r in recuperados if r["score_lex"] >= umbral]

    MAX_CANDIDATOS = 6
    candidatos_base = recuperados[:MAX_CANDIDATOS]

    if not candidatos_base:
        candidatos_base = [
            {"f": f, "score_lex": 0.0, "evidencias": []}
            for f in formatos[:MAX_CANDIDATOS]
        ]

    max_lex = max((x["score_lex"] for x in candidatos_base), default=1.0) or 1.0

    # 2) Candidatos con contenido RICO (menos candidatos, más contexto)
    candidatos = []
    for item in candidatos_base:
        f = item["f"]
        candidatos.append(
            {
                "id": f.id,
                "titulo": f.titulo or "",
                "tipo_juicio": f.tipo_juicio or "",
                # El resumen IA está estructurado con apartados. Enviarlo
                # completo da a la IA material para juzgar con precisión.
                "resumen_estructurado": (f.resumen_ia or f.descripcion or "")[:2500],
                "keywords": (f.palabras_clave or "")[:300],
                "descripcion": (f.descripcion or "")[:300],
                "fragmentos_relevantes": extraer_snippets_relevantes(
                    f.contenido_texto or "",
                    consulta,
                    None,
                    max_snippets=3,
                    window=450,
                    max_chars=1800,
                ),
                "recuperacion_local": round((item["score_lex"] / max_lex) * 100, 1),
            }
        )

    # 3) UNA SOLA llamada IA comprehensiva
    data = None
    if GROQ_API_KEY:
        try:
            data = llamar_groq_json(
                (
                    "Eres un abogado mexicano senior experto en análisis "
                    "comparativo de escritos judiciales y recuperación "
                    "documental forense. Trabajas EXCLUSIVAMENTE con la "
                    "evidencia proporcionada. No inventas contenido. "
                    "Responde siempre JSON válido."
                ),
                construir_prompt_reranking(consulta, candidatos),
                max_tokens=2500,
                temperature=0.1,
            )
        except Exception as e:
            print(f"⚠️ smart-search IA: {e}")
            try:
                db.add(
                    ActivityLog(
                        firm_id=user.firm_id,
                        user_id=user.id,
                        action="error_ia",
                        entity="FormatoDemanda",
                        description=f"Fallo IA en smart-search: {str(e)[:180]}",
                    )
                )
                db.commit()
            except Exception:
                db.rollback()

    # 4) Procesar respuesta
    mejores = []
    analisis = ""

    if isinstance(data, dict):
        analisis = (data.get("analisis") or "").strip()
        ids_validos = {c["id"] for c in candidatos}

        for item in data.get("mejores") or []:
            try:
                fid = int(item.get("id"))
                if fid not in ids_validos:
                    continue

                ai_score = max(0, min(100, int(item.get("score", 0))))
                compat = (item.get("compatibilidad") or "").strip().lower()

                base = next((c for c in candidatos if c["id"] == fid), None)
                if not base:
                    continue

                final_score = round(base["recuperacion_local"] * 0.30 + ai_score * 0.70)

                if compat == "incompatible":
                    final_score = min(final_score, 24)

                mejores.append(
                    {
                        "id": fid,
                        "score": final_score,
                        "score_ia": ai_score,
                        "score_recuperacion": base["recuperacion_local"],
                        "compatibilidad": compat or "no determinada",
                        "sirve_tal_cual": bool(item.get("sirve_tal_cual", False)),
                        "razon": (item.get("razon") or "").strip(),
                        "cumple": item.get("cumple") or [],
                        "faltantes": item.get("faltantes") or [],
                        "conflictos": item.get("conflictos") or [],
                        "evidencia": item.get("evidencia") or [],
                    }
                )
            except (TypeError, ValueError, AttributeError) as e:
                print(f"⚠️ Resultado IA inválido: {e}")
                continue

        mejores.sort(key=lambda x: x["score"], reverse=True)
    else:
        analisis = (
            "Análisis local completado. La IA no pudo completar la "
            "valoración jurídica; se muestran coincidencias documentales."
        )
        for c in candidatos[:5]:
            mejores.append(
                {
                    "id": c["id"],
                    "score": round(c["recuperacion_local"]),
                    "score_ia": None,
                    "score_recuperacion": c["recuperacion_local"],
                    "compatibilidad": "no determinada",
                    "sirve_tal_cual": False,
                    "razon": "Coincidencia documental encontrada.",
                    "cumple": [],
                    "faltantes": [],
                    "conflictos": [],
                    "evidencia": [],
                }
            )

    mejores = [m for m in mejores if m["score"] >= 40][:10]

    formatos_por_id = {f.id: f for f in formatos}
    resultados = []

    for m in mejores:
        f = formatos_por_id.get(m["id"])
        if not f:
            continue
        resultados.append(
            {
                "id": f.id,
                "titulo": f.titulo,
                "tipo_juicio": f.tipo_juicio or "",
                "resumen_ia": f.resumen_ia or f.descripcion or "",
                "palabras_clave": f.palabras_clave or "",
                "archivo_nombre_original": f.archivo_nombre_original or "",
                "descargas": f.descargas or 0,
                "created_at": f.created_at.strftime("%d/%m/%Y") if f.created_at else "",
                "updated_at": (
                    f.updated_at.strftime("%d/%m/%Y %H:%M") if f.updated_at else ""
                ),
                "score": m["score"],
                "score_ia": m["score_ia"],
                "score_recuperacion": m["score_recuperacion"],
                "compatibilidad": m["compatibilidad"],
                "sirve_tal_cual": m["sirve_tal_cual"],
                "razon": m["razon"],
                "cumple": m["cumple"],
                "faltantes": m["faltantes"],
                "conflictos": m["conflictos"],
                "evidencia": m["evidencia"],
            }
        )

    respuesta = {
        "analisis": analisis or "Análisis completado.",
        "total_analizados": len(formatos),
        "candidatos_recuperados": len(candidatos_base),
        "resultados": resultados,
    }

    _SMART_CACHE[cache_key] = (ahora, respuesta)
    if len(_SMART_CACHE) > _SMART_CACHE_MAX:
        _SMART_CACHE.popitem(last=False)

    return respuesta


# ============================================================
# SUBIR FORMATO
# ============================================================
@router.post("/upload")
async def upload_formato(
    request: Request,
    titulo: str = Form(...),
    descripcion: str = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    # Validar extensión
    nombre = file.filename or "formato.docx"
    if not nombre.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "Solo se aceptan archivos .docx o .doc")

    # Leer contenido
    await file.seek(0)
    contenido = await file.read()
    if len(contenido) > 15 * 1024 * 1024:
        raise HTTPException(400, "El archivo no puede pesar más de 15 MB")

    # Extraer texto + IA
    texto = extraer_texto_docx(contenido)

    if not texto:
        print(
            "ℹ️ Documento sin texto extraíble — el usuario deberá escribir la descripción manualmente."
        )

    ia = (
        generar_resumen_ia(texto)
        if texto
        else {"resumen": "", "palabras_clave": "", "tipo_juicio": "", "ok": False}
    )

    # Subir a R2
    key = f"formatos_demanda/{user.firm_id}/{uuid.uuid4().hex[:12]}_{nombre}"
    folder = key.rsplit("/", 1)[0]
    filename = key.rsplit("/", 1)[-1]
    upload_fileobj(io.BytesIO(contenido), folder, filename)

    # Determinar el mejor resumen disponible
    desc_limpia = (descripcion or "").strip()
    resumen_final = ia.get("resumen") or desc_limpia or texto[:300] or None

    # Guardar el texto completo (máx 80k chars para no reventar la BD)
    texto_guardar = (texto or "")[:80000] or None

    nuevo = FormatoDemanda(
        firm_id=user.firm_id,
        titulo=titulo.strip(),
        descripcion=desc_limpia or None,
        resumen_ia=resumen_final,
        palabras_clave=ia.get("palabras_clave") or None,
        tipo_juicio=ia.get("tipo_juicio") or None,
        contenido_texto=texto_guardar,
        archivo_key=key,
        archivo_nombre_original=nombre,
        archivo_peso_bytes=len(contenido),
        uploaded_by=user.id,
    )
    db.add(nuevo)
    db.flush()

    # Bitácora
    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="create",
            entity="FormatoDemanda",
            entity_id=nuevo.id,
            description=f"Subió formato de demanda '{titulo}'",
        )
    )
    db.commit()
    db.refresh(nuevo)

    return {
        "message": "Formato subido exitosamente",
        "id": nuevo.id,
        "resumen_ia": nuevo.resumen_ia,
        "ia_disponible": ia["ok"],
        "texto_extraido": bool(texto),
    }


# ============================================================
# ACTUALIZAR (título, descripción, o reemplazar archivo)
# ============================================================
@router.put("/update/{formato_id}")
async def update_formato(
    formato_id: int,
    request: Request,
    titulo: str = Form(None),
    descripcion: str = Form(None),
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    f = (
        db.query(FormatoDemanda)
        .filter(FormatoDemanda.id == formato_id, FormatoDemanda.firm_id == user.firm_id)
        .first()
    )
    if not f:
        raise HTTPException(404, "Formato no encontrado")

    cambios = []

    if titulo and titulo.strip() != f.titulo:
        cambios.append(f"título: '{f.titulo}' → '{titulo.strip()}'")
        f.titulo = titulo.strip()

    if descripcion is not None:
        f.descripcion = descripcion.strip() or None

    # Reemplazar archivo
    if file and file.filename:
        nombre = file.filename
        if not nombre.lower().endswith((".docx", ".doc")):
            raise HTTPException(400, "Solo se aceptan .docx o .doc")

        await file.seek(0)
        contenido = await file.read()
        if len(contenido) > 15 * 1024 * 1024:
            raise HTTPException(400, "Máximo 15 MB")

        # Borrar anterior de R2
        if f.archivo_key:
            try:
                delete_file(f.archivo_key)
            except Exception:
                pass

        # Subir nuevo
        key = f"formatos_demanda/{user.firm_id}/{uuid.uuid4().hex[:12]}_{nombre}"
        upload_fileobj(
            io.BytesIO(contenido), key.rsplit("/", 1)[0], key.rsplit("/", 1)[-1]
        )
        f.archivo_key = key
        f.archivo_nombre_original = nombre
        f.archivo_peso_bytes = len(contenido)

        # Regenerar IA y contenido (SIEMPRE que tengamos texto)
        texto = extraer_texto_docx(contenido)
        if texto:
            f.contenido_texto = texto[:80000]
            ia = generar_resumen_ia(texto)
            if ia["resumen"]:
                f.resumen_ia = ia["resumen"]
            if ia["palabras_clave"]:
                f.palabras_clave = ia["palabras_clave"]
            if ia["tipo_juicio"]:
                f.tipo_juicio = ia["tipo_juicio"]

        cambios.append("archivo reemplazado")

    # Fallback: si no hay síntesis y sí hay descripción, usar la descripción como síntesis
    if (not f.resumen_ia) and f.descripcion:
        f.resumen_ia = f.descripcion[:800]
        cambios.append("síntesis generada desde descripción")

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="update",
            entity="FormatoDemanda",
            entity_id=f.id,
            description=f"Actualizó formato '{f.titulo}': {', '.join(cambios) or 'sin cambios'}",
        )
    )
    db.commit()
    return {"message": "Formato actualizado", "id": f.id}


# ============================================================
# DESCARGAR (cuenta descargas + bitácora)
# ============================================================
@router.get("/download/{formato_id}")
async def download_formato(
    formato_id: int, request: Request, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    f = (
        db.query(FormatoDemanda)
        .filter(FormatoDemanda.id == formato_id, FormatoDemanda.firm_id == user.firm_id)
        .first()
    )
    if not f:
        raise HTTPException(404, "Formato no encontrado")

    f.descargas = (f.descargas or 0) + 1

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="download",
            entity="FormatoDemanda",
            entity_id=f.id,
            description=f"Descargó formato '{f.titulo}'",
        )
    )
    db.commit()

    # Descargar el archivo desde R2 y devolverlo con el nombre/MIME correctos
    try:
        resp = s3_client.get_object(Bucket=R2_BUCKET, Key=f.archivo_key)
        data = resp["Body"].read()
    except Exception as e:
        raise HTTPException(500, f"No se pudo leer el archivo desde R2: {e}")

    # MIME según extensión
    nombre = f.archivo_nombre_original or "formato.docx"
    ext = os.path.splitext(nombre)[1].lower()
    if ext == ".docx":
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif ext == ".doc":
        mime = "application/msword"
    else:
        mime = "application/octet-stream"

    # Sanitizar el nombre para el header HTTP
    safe_name = nombre.replace('"', "").replace("\\", "").replace("/", "_")

    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "no-store",
        },
    )


# ============================================================
# ELIMINAR
# ============================================================
@router.delete("/delete/{formato_id}")
async def delete_formato(
    formato_id: int, request: Request, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    f = (
        db.query(FormatoDemanda)
        .filter(FormatoDemanda.id == formato_id, FormatoDemanda.firm_id == user.firm_id)
        .first()
    )
    if not f:
        raise HTTPException(404, "Formato no encontrado")

    titulo = f.titulo
    archivo_key = f.archivo_key
    r2_ok = True
    r2_error = ""

    # 1) Intentar borrar de R2 PRIMERO
    if archivo_key:
        try:
            delete_file(archivo_key)
        except Exception as e:
            r2_ok = False
            r2_error = str(e)[:180]
            print(f"⚠️ No se pudo borrar de R2 {archivo_key}: {e}")

    # 2) Borrar de la base de datos siempre (no bloquear al usuario)
    db.delete(f)

    # 3) Registrar en bitácora (con aviso si R2 falló)
    descripcion = f"Eliminó formato '{titulo}'"
    if not r2_ok:
        descripcion += f" — ADVERTENCIA: archivo en R2 no eliminado ({r2_error})"

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="delete",
            entity="FormatoDemanda",
            entity_id=formato_id,
            description=descripcion,
        )
    )
    db.commit()

    return {
        "message": "Formato eliminado",
        "r2_ok": r2_ok,
        "warning": r2_error if not r2_ok else None,
    }


# ============================================================
# PREVIEW (URL prefirmada para mammoth.js)
# ============================================================
@router.get("/preview-url/{formato_id}")
async def preview_url(
    formato_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    f = (
        db.query(FormatoDemanda)
        .filter(
            FormatoDemanda.id == formato_id,
            FormatoDemanda.firm_id == user.firm_id,
        )
        .first()
    )
    if not f:
        raise HTTPException(404, "Formato no encontrado")

    return {"url": get_file_url(f.archivo_key, expires_in=600)}


# ============================================================
# VISTA PREVIA PDF — usando LibreOffice (preserva el formato original)
# ============================================================
@router.get("/preview-pdf/{formato_id}")
async def preview_pdf(
    formato_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Descarga el DOCX desde R2 a un temporal, lo convierte a PDF con
    LibreOffice (converter.word_to_pdf) y devuelve el PDF.

    - Los temporales (docx + pdf) se borran inmediatamente después de
      enviar la respuesta.
    - No se guarda nada permanente.
    """
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    f = (
        db.query(FormatoDemanda)
        .filter(
            FormatoDemanda.id == formato_id,
            FormatoDemanda.firm_id == user.firm_id,
        )
        .first()
    )
    if not f:
        raise HTTPException(404, "Formato no encontrado")

    # ── Preparar carpeta temporal ─────────────────────────────
    import os as _os

    TEMP_DIR = _os.path.join("storage", "temp")
    _os.makedirs(TEMP_DIR, exist_ok=True)

    # Extensión original (.docx o .doc) para que LibreOffice sepa qué hacer
    ext = _os.path.splitext(f.archivo_key)[1] or ".docx"
    tmp_docx = _os.path.join(TEMP_DIR, f"fmt_prev_{uuid.uuid4().hex}{ext}")

    pdf_path = None
    try:
        # ── Descargar el DOCX original desde R2 al temporal ───
        try:
            resp = s3_client.get_object(Bucket=R2_BUCKET, Key=f.archivo_key)
            data = resp["Body"].read()
        except Exception as e:
            raise HTTPException(500, f"No se pudo leer el archivo desde R2: {e}")

        with open(tmp_docx, "wb") as out:
            out.write(data)

        # ── Convertir a PDF con LibreOffice (preserva formato) ─
        pdf_path = converter.word_to_pdf(tmp_docx)
        if not pdf_path or not _os.path.exists(pdf_path):
            raise HTTPException(
                500,
                "No se pudo convertir el documento a PDF. "
                "Verifica que LibreOffice esté instalado en el servidor.",
            )

        # ── Leer el PDF a memoria y devolverlo ────────────────
        with open(pdf_path, "rb") as fp:
            pdf_bytes = fp.read()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="preview_{formato_id}.pdf"',
                "Cache-Control": "no-store",
            },
        )

    finally:
        # Limpieza inmediata: borrar el DOCX temporal y el PDF generado
        try:
            if _os.path.exists(tmp_docx):
                _os.remove(tmp_docx)
        except OSError:
            pass
        try:
            if pdf_path and _os.path.exists(pdf_path):
                _os.remove(pdf_path)
        except OSError:
            pass

        # ============================================================


# REINDEXAR — Extrae texto de los formatos que no lo tengan
# ============================================================
@router.post("/reindex")
async def reindex_formatos(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Recorre todos los formatos sin `contenido_texto` y les extrae el texto
    desde R2. No regenera la síntesis IA (solo el contenido plano) para
    que sea rápido.
    """
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    pendientes = (
        db.query(FormatoDemanda)
        .filter(
            FormatoDemanda.firm_id == user.firm_id,
            or_(
                FormatoDemanda.contenido_texto.is_(None),
                FormatoDemanda.contenido_texto == "",
            ),
        )
        .all()
    )

    procesados = 0
    errores = 0

    for f in pendientes:
        try:
            resp = s3_client.get_object(Bucket=R2_BUCKET, Key=f.archivo_key)
            data = resp["Body"].read()
            texto = extraer_texto_docx(data)
            if texto:
                f.contenido_texto = texto[:80000]
                procesados += 1
            else:
                errores += 1
        except Exception as e:
            print(f"⚠️ Error reindexando {f.id}: {e}")
            errores += 1

    db.commit()

    return {
        "ok": True,
        "total_pendientes": len(pendientes),
        "procesados": procesados,
        "errores": errores,
    }
