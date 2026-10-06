# app/utils/search.py
"""
Helpers para búsquedas seguras en la base de datos.

Problema que resuelve:
    Los endpoints de búsqueda hacían `int(search_term) if search_term.isdigit()`
    sin validar el rango. Cuando el usuario buscaba un teléfono (10 dígitos),
    PostgreSQL lanzaba:
        psycopg.errors.NumericValueOutOfRange: integer out of range
    porque INTEGER en PostgreSQL acepta máximo 2,147,483,647.

Solución:
    Un único helper que devuelve el número solo si es un entero válido
    dentro del rango. Si no, devuelve None y el filtro se omite.
"""

from typing import Optional

# Límite de INTEGER en PostgreSQL (2^31 - 1)
POSTGRES_INT_MAX = 2_147_483_647


def safe_int(valor, max_valor: int = POSTGRES_INT_MAX) -> Optional[int]:
    """
    Convierte un valor a int seguro para usar en filtros de PostgreSQL.
    Devuelve None si el valor no es un número, es negativo, o excede el rango
    permitido por la columna INTEGER.

    Uso típico:
        exp = safe_int(search_term)
        if exp is not None:
            condiciones.append(Model.expediente == exp)
    """
    if valor is None:
        return None

    limpio = str(valor).strip()
    if not limpio.isdigit():
        return None

    try:
        n = int(limpio)
        if 0 < n <= max_valor:
            return n
    except (ValueError, OverflowError):
        return None

    return None


def build_search_conditions(
    model, search_term: str, text_fields: list, int_fields: list = None
):
    """
    Construye la lista de condiciones `or_()` para una búsqueda.

    Parámetros:
        model         → clase del modelo SQLAlchemy (ej: Client)
        search_term   → texto que ingresó el usuario
        text_fields   → lista de columnas de texto a comparar con ILIKE
                        (ej: [Client.name, Client.paterno, ...])
        int_fields    → lista de columnas enteras a comparar con igualdad
                        SOLO si el término es un entero válido
                        (ej: [Client.expediente_interno])

    Devuelve: lista de condiciones lista para pasar a `or_(*condiciones)`.
    """
    condiciones = []

    # Filtros de texto: siempre aplican
    for columna in text_fields:
        condiciones.append(columna.ilike(f"%{search_term}%"))

    # Filtros numéricos: solo si el término es un entero válido
    if int_fields:
        numero = safe_int(search_term)
        if numero is not None:
            for columna in int_fields:
                condiciones.append(columna == numero)

    return condiciones
