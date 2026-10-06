"""
Conversión de números y fechas a texto en español para documentos legales mexicanos.

Casos cubiertos:
  • Números del 0 al 999,999,999,999 (con billones).
  • Apócope correcto: UNO→UN, VEINTIUNO→VEINTIÚN antes de MIL/MILLONES/PESOS.
  • Regla RAE del "de": solo se agrega cuando el monto es múltiplo exacto de 1,000,000
    (ej. "UN MILLÓN DE PESOS" vs "UN MILLÓN QUINIENTOS MIL PESOS").
  • Fechas: "un día" (singular), "veintiún días", "treinta y un días".
  • Años en letras minúsculas: "dos mil veintiséis", "mil novecientos noventa y nueve".

Ejemplos verificados:
    >>> numero_a_letra(20000)     # 'VEINTE MIL PESOS 00/100 M.N.'
    >>> numero_a_letra(21000)     # 'VEINTIÚN MIL PESOS 00/100 M.N.'
    >>> numero_a_letra(1)         # 'UN PESO 00/100 M.N.'
    >>> numero_a_letra(1.5)       # 'UN PESO 50/100 M.N.'
    >>> numero_a_letra(101000)    # 'CIENTO UN MIL PESOS 00/100 M.N.'
    >>> numero_a_letra(1000000)   # 'UN MILLÓN DE PESOS 00/100 M.N.'
    >>> numero_a_letra(1500000)   # 'UN MILLÓN QUINIENTOS MIL PESOS 00/100 M.N.'
    >>> fecha_a_letra(datetime(2026, 10, 2))  # 'dos días del mes de octubre de dos mil veintiséis'
    >>> fecha_a_letra(datetime(2026, 10, 1))  # 'un día del mes de octubre de dos mil veintiséis'
    >>> fecha_a_letra(datetime(2026, 10, 21)) # 'veintiún días del mes de octubre de dos mil veintiséis'
"""

from datetime import date, datetime

# ══════════════════════════════════════════════════════════════════
# TABLAS BASE
# ══════════════════════════════════════════════════════════════════

UNIDADES = [
    "",
    "UNO",
    "DOS",
    "TRES",
    "CUATRO",
    "CINCO",
    "SEIS",
    "SIETE",
    "OCHO",
    "NUEVE",
    "DIEZ",
    "ONCE",
    "DOCE",
    "TRECE",
    "CATORCE",
    "QUINCE",
    "DIECISÉIS",
    "DIECISIETE",
    "DIECIOCHO",
    "DIECINUEVE",
    "VEINTE",
]

# Del 21 al 29 se escriben en UNA sola palabra, sin "y".
VEINTI = {
    21: "VEINTIUNO",
    22: "VEINTIDÓS",
    23: "VEINTITRÉS",
    24: "VEINTICUATRO",
    25: "VEINTICINCO",
    26: "VEINTISÉIS",
    27: "VEINTISIETE",
    28: "VEINTIOCHO",
    29: "VEINTINUEVE",
}

DECENAS = [
    "",
    "",
    "VEINTE",
    "TREINTA",
    "CUARENTA",
    "CINCUENTA",
    "SESENTA",
    "SETENTA",
    "OCHENTA",
    "NOVENTA",
]

CENTENAS = [
    "",
    "CIENTO",
    "DOSCIENTOS",
    "TRESCIENTOS",
    "CUATROCIENTOS",
    "QUINIENTOS",
    "SEISCIENTOS",
    "SETECIENTOS",
    "OCHOCIENTOS",
    "NOVECIENTOS",
]

MESES = [
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]

# Días del mes en letras (para fechas). 21 = "veintiún", 31 = "treinta y un".
DIAS_LETRA = [
    "",
    "un",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
    "veinte",
    "veintiún",
    "veintidós",
    "veintitrés",
    "veinticuatro",
    "veinticinco",
    "veintiséis",
    "veintisiete",
    "veintiocho",
    "veintinueve",
    "treinta",
    "treinta y un",
]

# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════


def _apocope_final(texto: str) -> str:
    """Aplica apócope a la última palabra si termina en UNO o VEINTIUNO.

    Ejemplos:
        'UNO'                → 'UN'
        'VEINTIUNO'          → 'VEINTIÚN'
        'CIENTO UNO'         → 'CIENTO UN'
        'TREINTA Y UNO'      → 'TREINTA Y UN'
        'DOS'                → 'DOS'  (sin cambios)
        'MIL QUINIENTOS'     → 'MIL QUINIENTOS'  (sin cambios)
    """
    if not texto:
        return texto
    if texto.endswith("VEINTIUNO"):
        # 'VEINTIUNO' → 'VEINTIÚN'  (quita las 3 últimas letras 'UNO' y añade 'ÚN')
        return texto[:-3] + "ÚN"
    if texto.endswith("UNO"):
        # 'UNO' → 'UN'  (quita la última 'O')
        return texto[:-1]
    return texto


def _decenas_unidades(n: int) -> str:
    """Convierte un número de 1 a 99 a letras, SIN apócope."""
    if n == 0:
        return ""
    if n <= 20:
        return UNIDADES[n]
    if 21 <= n <= 29:
        return VEINTI[n]
    d = n // 10
    u = n % 10
    if u == 0:
        return DECENAS[d]
    return f"{DECENAS[d]} Y {UNIDADES[u]}"


def _grupo_centenas(n: int, apocope: bool = False) -> str:
    """Convierte un número de 1 a 999 a letras.

    apocope=True se usa cuando este grupo va seguido de MIL o MILLONES.
    """
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    c = n // 100
    resto = n % 100
    partes = []
    if c:
        partes.append(CENTENAS[c])
    if resto:
        texto = _decenas_unidades(resto)
        if apocope:
            texto = _apocope_final(texto)
        partes.append(texto)
    return " ".join(partes)


def _grupo_miles(n: int) -> str:
    """Convierte un número de 1 a 999,999 a letras."""
    if n == 0:
        return ""
    miles = n // 1000
    resto = n % 1000
    partes = []
    if miles:
        if miles == 1:
            partes.append("MIL")
        else:
            # El grupo 'miles' va seguido de MIL → apócope=True.
            partes.append(f"{_grupo_centenas(miles, apocope=True)} MIL")
    if resto:
        partes.append(_grupo_centenas(resto))
    return " ".join(partes)


def _grupo_millones(n: int) -> str:
    """Convierte un número de 1,000,000 a 999,999,999 a letras."""
    if n == 0:
        return ""
    millones = n // 1_000_000
    resto = n % 1_000_000
    partes = []
    if millones:
        if millones == 1:
            partes.append("UN MILLÓN")
        else:
            # El grupo 'millones' va seguido de MILLONES → apócope final.
            texto_millones = _grupo_miles(millones)
            texto_millones = _apocope_final(texto_millones)
            partes.append(f"{texto_millones} MILLONES")
    if resto:
        partes.append(_grupo_miles(resto))
    return " ".join(partes)


def _grupo_miles_millones(n: int) -> str:
    """Convierte un número de 1,000,000,000 a 999,999,999,999 a letras."""
    if n == 0:
        return ""
    miles_millones = n // 1_000_000_000
    resto = n % 1_000_000_000
    partes = []
    if miles_millones == 1:
        partes.append("MIL MILLONES")
    else:
        texto = _grupo_miles(miles_millones)
        texto = _apocope_final(texto)
        partes.append(f"{texto} MIL MILLONES")
    if resto:
        partes.append(_grupo_millones(resto))
    return " ".join(partes)


# ══════════════════════════════════════════════════════════════════
# API PÚBLICA — NÚMEROS
# ══════════════════════════════════════════════════════════════════


def numero_a_letra_simple(cantidad) -> str:
    """Convierte un número entero a letras, sin sufijo de moneda.

    Ejemplos:
        0          → 'CERO'
        1          → 'UNO'
        21         → 'VEINTIUNO'
        101        → 'CIENTO UNO'
        2026       → 'DOS MIL VEINTISÉIS'
        16000      → 'DIECISÉIS MIL'
        21000      → 'VEINTIÚN MIL'
        101000     → 'CIENTO UN MIL'
        1000000    → 'UN MILLÓN'
        1500000    → 'UN MILLÓN QUINIENTOS MIL'
        2000000    → 'DOS MILLONES'
        1000000000 → 'MIL MILLONES'
    """
    try:
        n = int(round(float(cantidad)))
    except (TypeError, ValueError):
        return ""

    if n < 0:
        return "MENOS " + numero_a_letra_simple(abs(n))
    if n == 0:
        return "CERO"
    if n < 1_000:
        return _grupo_centenas(n)
    if n < 1_000_000:
        return _grupo_miles(n)
    if n < 1_000_000_000:
        return _grupo_millones(n)
    if n < 1_000_000_000_000:
        return _grupo_miles_millones(n)
    return "CANTIDAD NO SOPORTADA"


def numero_a_letra(cantidad) -> str:
    """Convierte una cantidad monetaria a letras con formato M.N.

    Reglas aplicadas:
      • Apócope final: 'UNO'→'UN', 'VEINTIUNO'→'VEINTIÚN' antes de 'PESOS'.
      • Concordancia: 'PESO' (singular) vs 'PESOS' (plural).
      • Regla RAE del 'de': se agrega 'DE' únicamente cuando el monto es
        múltiplo exacto de 1,000,000 (ej. 'UN MILLÓN DE PESOS').

    Ejemplos:
        0.00      → 'CERO PESOS 00/100 M.N.'
        1.00      → 'UN PESO 00/100 M.N.'
        1.50      → 'UN PESO 50/100 M.N.'
        21.00     → 'VEINTIÚN PESOS 00/100 M.N.'
        101.00    → 'CIENTO UN PESOS 00/100 M.N.'
        16000.00  → 'DIECISÉIS MIL PESOS 00/100 M.N.'
        21000.00  → 'VEINTIÚN MIL PESOS 00/100 M.N.'
        101000.00 → 'CIENTO UN MIL PESOS 00/100 M.N.'
        1000000   → 'UN MILLÓN DE PESOS 00/100 M.N.'
        1500000   → 'UN MILLÓN QUINIENTOS MIL PESOS 00/100 M.N.'
        2000000   → 'DOS MILLONES DE PESOS 00/100 M.N.'
    """
    try:
        cantidad = round(float(cantidad), 2)
    except (TypeError, ValueError):
        return ""

    if cantidad < 0:
        return "MENOS " + numero_a_letra(abs(cantidad))

    entero = int(cantidad)
    centavos = int(round((cantidad - entero) * 100))

    # Si por redondeo los centavos llegan a 100, ajustamos.
    if centavos == 100:
        entero += 1
        centavos = 0

    if entero == 0:
        letras = "CERO"
    else:
        letras = numero_a_letra_simple(entero)
        # Apócope final antes de 'PESOS' (sustantivo masculino plural):
        # 'UNO' → 'UN', 'VEINTIUNO' → 'VEINTIÚN', 'CIENTO UNO' → 'CIENTO UN'.
        letras = _apocope_final(letras)

    moneda = "PESO" if entero == 1 else "PESOS"

    # Regla RAE: 'de' solo cuando el monto es múltiplo exacto de 1,000,000.
    if entero >= 1_000_000 and entero % 1_000_000 == 0:
        return f"{letras} DE {moneda} {centavos:02d}/100 M.N."

    return f"{letras} {moneda} {centavos:02d}/100 M.N."


# ══════════════════════════════════════════════════════════════════
# API PÚBLICA — FECHAS
# ══════════════════════════════════════════════════════════════════


def _dia_a_letra(dia: int) -> str:
    """Día del mes en letras.

    Ejemplos:
        1  → 'un'
        2  → 'dos'
        21 → 'veintiún'
        31 → 'treinta y un'
    """
    if 1 <= dia <= 31:
        return DIAS_LETRA[dia]
    return str(dia)


def _anio_a_letra(anio: int) -> str:
    """Año en letras minúsculas.

    Ejemplos:
        2026 → 'dos mil veintiséis'
        2001 → 'dos mil uno'
        2000 → 'dos mil'
        1999 → 'mil novecientos noventa y nueve'
        1000 → 'mil'
    """
    return numero_a_letra_simple(anio).lower()


def fecha_a_letra(fecha) -> str:
    """Fecha larga en letras para contratos y escritos.

    Ejemplos:
        date(2026, 10, 2)  → 'dos días del mes de octubre de dos mil veintiséis'
        date(2026, 10, 1)  → 'un día del mes de octubre de dos mil veintiséis'
        date(2026, 10, 21) → 'veintiún días del mes de octubre de dos mil veintiséis'
        date(2026, 10, 31) → 'treinta y un días del mes de octubre de dos mil veintiséis'
    """
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    if not isinstance(fecha, date):
        return ""

    dia = fecha.day
    mes = MESES[fecha.month]
    anio_letra = _anio_a_letra(fecha.year)
    dia_letra = _dia_a_letra(dia)
    sustantivo = "día" if dia == 1 else "días"
    return f"{dia_letra} {sustantivo} del mes de {mes} de {anio_letra}"


def fecha_corta_a_letra(fecha) -> str:
    """Fecha corta en letras (sin 'del mes de').

    Ejemplo:
        date(2026, 10, 2) → 'dos de octubre de dos mil veintiséis'
    """
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    if not isinstance(fecha, date):
        return ""
    dia = fecha.day
    mes = MESES[fecha.month]
    anio_letra = _anio_a_letra(fecha.year)
    return f"{_dia_a_letra(dia)} de {mes} de {anio_letra}"
