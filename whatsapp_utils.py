import os
import httpx
from dotenv import load_dotenv
import time
import uuid
from logging_utils import get_logger, log_event

load_dotenv()

EVO_URL = os.environ.get("EVOLUTION_API_URL")
EVO_TOKEN = os.environ.get("EVOLUTION_API_TOKEN")
INSTANCE_NAME = os.environ.get("EVOLUTION_INSTANCE_NAME")

logger = get_logger("sofia.evolution")


async def enviar_mensaje_whatsapp(telefono_destino: str, texto: str):
    url = f"{EVO_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVO_TOKEN,
        "Content-Type": "application/json"
    }

    telefono_limpio = telefono_destino.replace("+", "")

    payload = {
        "number": telefono_limpio,
        "text": texto,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }

    # AÑADIMOS UN TIMEOUT MÁS LARGO (ej: 30 segundos) para aguantar picos de peticiones
    timeout = httpx.Timeout(30.0)

    call_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    log_event(
        logger,
        "INFO",
        "evolution.call.start",
        call_id=call_id,
        url=url,
        telefono_destino=telefono_destino,
        telefono_limpio=telefono_limpio,
        instance_name=INSTANCE_NAME,
        text_resumen=texto[:300],
        text_length=len(texto or ""),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_event(
            logger,
            "ERROR",
            "evolution.call.exception",
            call_id=call_id,
            url=url,
            telefono_destino=telefono_destino,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000

    if response.status_code not in [200, 201]:
        log_event(
            logger,
            "WARNING",
            "evolution.call.error_status",
            call_id=call_id,
            status_code=response.status_code,
            duration_ms=duration_ms,
            response_text_resumen=response.text[:500],
        )
    else:
        log_event(
            logger,
            "INFO",
            "evolution.call.end",
            call_id=call_id,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    response.raise_for_status()
