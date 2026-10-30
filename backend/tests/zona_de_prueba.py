"""Las zonas con las que los tests construyen horas. Las mismas de la 0060."""

from app.core.zona import zona

# Lo que antes era `horario_activo.ZONA_SALON`: la de casi todos los salones.
ZONA_SALON = zona("Europe/Madrid")
CANARIAS = zona("Atlantic/Canary")
