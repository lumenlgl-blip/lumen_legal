"""
Conversor de documentos Office → PDF usando LibreOffice headless.

Soluciona el problema de que 'soffice' no esté en el PATH (típico en Windows).
Busca LibreOffice en las rutas típicas y muestra logs claros de lo que pasa.

Soporta:
  Word:    .doc .docx .docm .dot .dotx .dotm .rtf .odt .ott
  Excel:   .xls .xlsx .xlsm .xlsb .ods .ots
  PPT:     .ppt .pptx .pptm .pps .ppsx .odp .otp
"""
import subprocess
import os
import shutil
import platform
import logging

logger = logging.getLogger(__name__)


# ============================================================
# 🚨 RUTA FORZADA — Ajústala si tu LibreOffice está en otro sitio
# ============================================================
_SOFFICE_ABSOLUTE_PATH = r"C:\Program Files\LibreOffice\program\soffice.exe"
# ============================================================


_WINDOWS_CANDIDATES = [
    _SOFFICE_ABSOLUTE_PATH,
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    r"C:\Program Files\LibreOffice 7\program\soffice.exe",
    r"C:\Program Files\LibreOffice 24\program\soffice.exe",
    r"C:\Program Files\LibreOffice 25\program\soffice.exe",
    r"C:\Program Files\LibreOffice 26\program\soffice.exe",
]
_LINUX_CANDIDATES = [
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
]
_MAC_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_soffice() -> str | None:
    """Devuelve la ruta completa a soffice, o None si no lo encuentra."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found and os.path.exists(found):
        return found

    system = platform.system()
    if system == "Windows":
        candidates = _WINDOWS_CANDIDATES
    elif system == "Darwin":
        candidates = _MAC_CANDIDATES
    else:
        candidates = _LINUX_CANDIDATES

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


_SOFFICE_PATH: str | None = None


def _get_soffice() -> str | None:
    global _SOFFICE_PATH
    if _SOFFICE_PATH is None:
        _SOFFICE_PATH = find_soffice()
        if _SOFFICE_PATH:
            logger.info(f"✅ LibreOffice encontrado en: {_SOFFICE_PATH}")
        else:
            logger.warning(
                "⚠️ LibreOffice NO encontrado.\n"
                "   Edita _SOFFICE_ABSOLUTE_PATH en app/services/converter.py\n"
                "   Ruta típica Windows: C:\\Program Files\\LibreOffice\\program\\soffice.exe"
            )
    return _SOFFICE_PATH


WORD_EXTS  = (".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
              ".rtf", ".odt", ".ott")
EXCEL_EXTS = (".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".ots")
PPT_EXTS   = (".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp", ".otp")
OFFICE_EXTS = WORD_EXTS + EXCEL_EXTS + PPT_EXTS


def word_to_pdf(src_path: str) -> str | None:
    """
    Convierte CUALQUIER documento Office/OpenDocument a PDF usando
    LibreOffice headless. Devuelve la ruta del PDF o None si falla.
    """
    soffice = _get_soffice()
    if not soffice:
        logger.error("No se puede convertir: LibreOffice no disponible.")
        return None

    src_path = os.path.abspath(src_path)
    out_dir = os.path.dirname(src_path)

    base = os.path.splitext(os.path.basename(src_path))[0]
    expected_pdf = os.path.join(out_dir, base + ".pdf")

    if os.path.exists(expected_pdf):
        try:
            os.remove(expected_pdf)
        except OSError:
            pass

    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to", "pdf",
        "--outdir", out_dir,
        src_path,
    ]

    logger.info(f"Ejecutando: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=False,
            timeout=180,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
    except subprocess.TimeoutExpired:
        logger.error("LibreOffice tardó más de 180s.")
        return None
    except Exception as e:
        logger.error(f"Error ejecutando LibreOffice: {e}")
        return None

    if os.path.exists(expected_pdf):
        logger.info(f"✅ PDF generado: {expected_pdf}")
        return expected_pdf

    logger.error(
        "❌ LibreOffice no generó el PDF.\n"
        f"   code:   {result.returncode}\n"
        f"   stdout: {result.stdout.decode(errors='ignore')[:500]}\n"
        f"   stderr: {result.stderr.decode(errors='ignore')[:500]}"
    )
    return None