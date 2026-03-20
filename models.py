from pydantic import BaseModel, Field
from typing import Optional

class SolicitudChat(BaseModel):
    name: str
    phone: str
    role: str = Field(description="Puede ser 'coder' o 'staff'")
    clan: Optional[str] = Field(default=None, description="Clan de Riwi (ej: hamilton, thompson, tesla, etc.)")
    advancedPath: Optional[str] = Field(default=None, description="Ruta avanzada (ej: Java con Springboot, TS con Next, etc.)")

class RespuestaIA(BaseModel):
    respuesta_ia_para_usuario: str
    estado_conversacion: str = Field(
        description="Debe ser 'EN_CURSO' mientras charlan, o 'FINALIZADA' si el usuario se despide explícitamente."
    )
