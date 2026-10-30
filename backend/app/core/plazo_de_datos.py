"""El plazo de los datos: 90 días desde que la suscripción termina de verdad.

QUIÉN ESTÁ EN PLAZO Y CUÁNDO SE BORRA NO SE CALCULA AQUÍ. Lo calcula la base,
en un solo sitio (`relojes_del_plazo` y `cuentas_fuera_de_plazo`, 0068), porque
el borrado es una función de la base y no puede preguntarle a Python: si las
fechas se calcularan en dos sitios, el correo podría decir un día y el borrado
hacer otro. Aquí queda lo que es solo de los avisos: qué aviso toca y cómo se
escribe la fecha.

LAS FECHAS SON DÍAS DEL CALENDARIO DE LA CUENTA (su zona horaria, la de su
centro de referencia), no horas: el correo dice «el 2 de enero de 2027» y eso
tiene que ser verdad allí. El día que termina la suscripción es el día 0; el
borrado es el día 90.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

# El mismo número que `relojes_del_plazo` (0068): el guarda los compara.
PLAZO_DIAS = 90

# Una suscripción en estos estados ha VUELTO: el reloj se para.
VIVA = frozenset({"trialing", "active"})

Aviso = Literal["terminada", "30", "7"]

# Columna de sello de cada aviso.
SELLO: dict[Aviso, str] = {"terminada": "aviso_terminada_at", "30": "aviso_30_at", "7": "aviso_7_at"}


@dataclass(frozen=True)
class Plazo:
    terminada_el: date
    se_borra_el: date
    dias_restantes: int


def aviso_que_toca(p: Plazo, enviados: dict[Aviso, bool]) -> Aviso | None:
    """El aviso que hay que mandar AHORA, o ninguno. Uno como mucho por pasada.

    LA ELEGIBILIDAD ES MONÓTONA, como en los recordatorios: un aviso toca desde
    que entra en su franja y hasta que se manda, así que un cron caído RETRASA
    un aviso, no lo pierde. Pero las franjas no se pisan: si el cron estuvo
    caído desde el día 70 hasta el 85, al volver toca el de 7 días, no el de 30
    —mandar los dos el mismo día sería ruido, y el de 7 dice lo que importa—.

      · «terminada»: mientras queden más de 30 días.
      · «30»: quedando entre 30 y 8.
      · «7»: quedando entre 7 y 0.
    Pasado el día del borrado no toca ninguno.
    """
    d = p.dias_restantes
    if d < 0:
        return None
    if d <= 7:
        return None if enviados.get("7") else "7"
    if d <= 30:
        return None if enviados.get("30") else "30"
    return None if enviados.get("terminada") else "terminada"


def fecha_larga(d: date) -> str:
    """«2 de enero de 2027». Sin depender del locale del servidor."""
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre")
    return f"{d.day} de {meses[d.month - 1]} de {d.year}"
