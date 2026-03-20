import logging
import json
import os
from typing import Any, Dict


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _ensure_basic_config() -> None:
    # Configure basic logging only once
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=LOG_LEVEL,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger configurado para el proyecto.

    Usa un formato simple pero consistente, y permite adjuntar un dict JSON
    serializado en el mensaje mediante `log_event`.
    """
    _ensure_basic_config()
    return logging.getLogger(name)


def _safe_json(data: Dict[str, Any]) -> str:
    """Convierte un dict a JSON de forma segura, usando repr como fallback."""
    try:
        return json.dumps(data, ensure_ascii=False)
    except TypeError:
        safe_data = {k: repr(v) for k, v in data.items()}
        return json.dumps(safe_data, ensure_ascii=False)


def log_event(logger: logging.Logger, level: str, event: str, **fields: Any) -> None:
    """Loguea un evento estructurado con un campo `event` y datos extra.

    Ejemplo:
        log_event(logger, "INFO", "openai.call.start", telefono="57300...", model="gpt-4o-mini")
    """
    payload = {"event": event, **fields}
    msg = _safe_json(payload)
    level = level.upper()

    if level == "DEBUG":
        logger.debug(msg)
    elif level == "INFO":
        logger.info(msg)
    elif level == "WARNING":
        logger.warning(msg)
    elif level == "ERROR":
        logger.error(msg)
    elif level == "CRITICAL":
        logger.critical(msg)
    else:
        logger.info(msg)
