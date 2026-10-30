from pydantic import BaseModel, Field, field_validator


class PuestoIn(BaseModel):
    """Un puesto de trabajo del salón. NO da permisos de ningún tipo.

    Solo la administradora tiene cuenta (0069); esto es solo el título que se
    ve en la ficha del empleado. Ver 0027_puestos.sql.
    """

    nombre: str = Field(min_length=1, max_length=60)

    @field_validator("nombre")
    @classmethod
    def _limpiar(cls, v: str) -> str:
        # Se recorta antes de validar el largo y antes de guardar: si no,
        # « Barbero» y «Barbero» entrarían como dos puestos distintos pese al
        # índice único, que compara ya recortado.
        limpio = v.strip()
        if not limpio:
            raise ValueError("Escribe el nombre del puesto.")
        return limpio


class PuestoOut(BaseModel):
    id: str
    nombre: str
    # Cuántos empleados lo tienen. Es lo que decide si el botón de borrar está
    # disponible, y evita que el frontend tenga que cruzar las dos listas.
    empleados: int = 0
