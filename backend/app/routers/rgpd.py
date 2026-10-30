"""Escudo RGPD: descarga de plantillas legales en PDF (admin-only).

El doc_id se valida contra el catálogo fijo del repo (fuera del set → 404)
y el cuerpo legal no admite input del usuario: los únicos datos variables
son los del salón, leídos de la BD por el salon_id del JWT verificado
(NUNCA del cliente) y saneados en el renderizador.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool

from app.core import publico
from app.core.admin_security import AdminLoginRateLimiter
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.core.cuenta import con_la_cuenta_dentro
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.rgpd.plantillas import PLANTILLAS
from app.rgpd.render import render_pdf

router = APIRouter(prefix="/rgpd", tags=["rgpd"], dependencies=[publico.ADMINISTRADORA])

# Generar PDFs es CPU: ventana deslizante por salón con el mismo mecanismo
# que el login del super admin. 15 descargas / 5 min sobran para uso real.
_pdf_rate_limiter = AdminLoginRateLimiter(max_fails=15, window_minutes=5)


@router.get(
    "/documentos/{doc_id}/pdf",
    response_class=Response,
    summary="Descarga una plantilla legal RGPD en PDF",
    description=(
        "Admin-only, tenant por salon_id del JWT. Genera la plantilla indicada "
        "(ids válidos: tratamiento, privacidad, imagen, baja) rellenada con los "
        "datos del salón (nombre comercial, identificación fiscal, dirección y "
        "fecha de generación). Sin identificación fiscal, esa línea no se "
        "imprime; sin dirección, se pinta como línea en blanco rellenable. "
        "Los campos del cliente van siempre en "
        "blanco. Cada página lleva un pie de descargo (no constituye asesoría "
        "legal). Rate limit ligero por salón (15 descargas / 5 min)."
    ),
    responses={
        200: {
            "description": "PDF de la plantilla (attachment)",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "doc_id fuera del catálogo o salón no encontrado"},
        429: {"description": "Límite de descargas alcanzado (cabecera Retry-After)"},
    },
)
async def descargar_documento_rgpd(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> Response:
    plantilla = PLANTILLAS.get(doc_id)
    if plantilla is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")

    bloqueado, retry_after = _pdf_rate_limiter.is_blocked(user.salon_id)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas descargas de documentos. Inténtalo de nuevo en unos minutos.",
            headers={"Retry-After": str(retry_after)},
        )
    _pdf_rate_limiter.add_failure(user.salon_id)

    try:
        rows = await db.select(
            "salons",
            {
                "select": "nombre_comercial,direccion,zona_horaria,accounts(cif)",
                "id": f"eq.{user.salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el perfil del salón",
        ) from e
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")

    # La generación es síncrona y CPU-bound: fuera del event loop.
    # El CIF es de la cuenta desde la 0040; el PDF lo sigue enseñando igual.
    pdf_bytes = await run_in_threadpool(
        render_pdf, plantilla, con_la_cuenta_dentro(rows[0])
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{plantilla.filename}"',
            "Cache-Control": "no-store",
        },
    )
