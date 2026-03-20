import os
import time
import asyncio
from fastapi import FastAPI, Request
from models import SolicitudChat
from ai_agent import procesar_mensaje, generar_prompt_chat, generar_prompt_faq, sesiones_activas
from whatsapp_utils import enviar_mensaje_whatsapp
from dotenv import load_dotenv
from logging_utils import get_logger, log_event
import uuid

logger = get_logger("sofia.api")

load_dotenv()
app = FastAPI(title="Sofia Chat Demo Promise")

# --- DEBOUNCE: Acumulador de mensajes por número ---
# { telefono: {"timer": asyncio.Task, "messages": [str]} }
pending_messages: dict = {}
DEBOUNCE_SECONDS = 3  # Tiempo de espera antes de procesar mensajes acumulados


async def flush_messages(telefono: str):
    """Espera DEBOUNCE_SECONDS y luego procesa todos los mensajes acumulados como uno solo."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    if telefono not in pending_messages:
        return

    mensajes_acumulados = pending_messages.pop(telefono)["messages"]
    texto_combinado = "\n".join(mensajes_acumulados)

    await procesar_y_responder(telefono, texto_combinado)


async def encolar_mensaje(telefono: str, texto: str):
    """Recibe un mensaje y lo acumula. Reinicia el timer si ya había uno corriendo."""
    if telefono in pending_messages:
        # Cancela el timer anterior y agrega el nuevo mensaje a la lista
        pending_messages[telefono]["timer"].cancel()
        pending_messages[telefono]["messages"].append(texto)
    else:
        pending_messages[telefono] = {"messages": [texto], "timer": None}

    # Lanza un nuevo timer
    timer = asyncio.create_task(flush_messages(telefono))
    pending_messages[telefono]["timer"] = timer


async def procesar_y_responder(telefono: str, texto: str):
    """Lógica central: llama a la IA y envía la respuesta. Separada del webhook."""
    log_event(
        logger,
        "INFO",
        "chat.user_message",
        telefono=telefono,
        texto_resumen=texto[:300],
        texto_length=len(texto),
    )

    resultado = await procesar_mensaje(telefono, texto)

    log_event(
        logger,
        "INFO",
        "chat.ia_decision",
        telefono=telefono,
        estado_conversacion=resultado.estado_conversacion,
        respuesta_length=len(resultado.respuesta_ia_para_usuario or ""),
    )

    await enviar_mensaje_whatsapp(telefono, resultado.respuesta_ia_para_usuario)

    if resultado.estado_conversacion == "FINALIZADA":
        log_event(
            logger,
            "INFO",
            "chat.ended",
            telefono=telefono,
            reason="estado_finalizada",
        )
        if telefono in sesiones_activas:
            del sesiones_activas[telefono]


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware para loguear cada request entrante y su tiempo de respuesta."""
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else None

    log_event(
        logger,
        "INFO",
        "request.incoming",
        request_id=request_id,
        method=method,
        path=path,
        client_ip=client_ip,
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_event(
            logger,
            "ERROR",
            "request.error",
            request_id=request_id,
            method=method,
            path=path,
            client_ip=client_ip,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000
    log_event(
        logger,
        "INFO",
        "request.completed",
        request_id=request_id,
        method=method,
        path=path,
        client_ip=client_ip,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )

    return response


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post("/solicitar-chat")
async def iniciar_chat(solicitud: SolicitudChat):
    """Recibe la orden para iniciar el chat de demostración con un inscrito a la presentación."""
    telefono = solicitud.phone.replace("+", "").replace(" ", "")

    log_event(
        logger,
        "INFO",
        "chat.start",
        telefono=telefono,
        name=solicitud.name,
        role=solicitud.role,
        clan=solicitud.clan,
        advancedPath=solicitud.advancedPath,
    )

    contexto = solicitud.model_dump()
    sesiones_activas[telefono] = {
        "es_faq": False,
        "contexto_original": contexto,
        "ultima_actividad": time.time(),
        "historial": [
            {"role": "system", "content": generar_prompt_chat(contexto)}
        ]
    }

    if solicitud.role.lower() == "coder":
        clan_str = solicitud.clan.capitalize() if solicitud.clan else "tu clan"
        ruta_str = solicitud.advancedPath if solicitud.advancedPath else "tu nueva ruta"

        primer_mensaje = (
            f"¡Hola {solicitud.name}! 👋 Soy Sofía, el agente conversacional desarrollado por el equipo de *Promise* "
            f"\n\n¡Qué emoción tenerte en nuestra demo interactiva! Además, aprovecho para felicitarte "
            f"porque a partir de la próxima semana inicias tu ruta avanzada en *{ruta_str}*. ¡Mucho éxito en este nuevo reto! 🚀\n\n"
            f"Hoy estoy aquí para mostrarte de qué estoy hecha. Puedes preguntarme sobre cómo funciono internamente, mi arquitectura de microservicios, el problema que resolvimos para RiwiCall o cualquier detalle de nuestro proyecto. ¿De qué te gustaría hablar?"
        )
    else:
        primer_mensaje = (
            f"¡Hola {solicitud.name}! 👋 Soy Sofía, el primer agente en producción de *Promise*, y es un honor tener a parte del *Staff* "
            f"en nuestra demostración de hoy.\n\nEstoy conectada a nuestra arquitectura modular y lista para responder tus dudas. "
            f"Pregúntame sobre nuestro impacto (como la reducción del 86% en costos operativos), la arquitectura de Node.js, "
            f"nuestro modelo híbrido o la historia de la empresa. ¿Por dónde empezamos? 🤖✨"
        )

    sesiones_activas[telefono]["historial"].append({"role": "assistant", "content": primer_mensaje})
    await enviar_mensaje_whatsapp(telefono, primer_mensaje)

    return {"status": "ok", "mensaje": f"Demo chat iniciado con {solicitud.name}"}


@app.post("/webhook")
async def recibir_mensaje_whatsapp(request: Request):
    """Recibe los mensajes de texto que el usuario envía por WhatsApp (Vía Evolution API)."""
    try:
        body = await request.json()

        log_event(
            logger,
            "INFO",
            "webhook.received",
            raw_event=body.get("event"),
        )

        evento = body.get("event", "")
        if evento not in ["messages.upsert", "MESSAGES_UPSERT"]:
            log_event(
                logger,
                "INFO",
                "webhook.ignored",
                reason="No es un evento de mensaje",
                raw_event=evento,
            )
            return {"status": "ignorado", "reason": "No es un evento de mensaje"}

        data = body.get("data", {})
        msg_data = data.get("message", data) if "key" not in data else data

        key = msg_data.get("key", {})
        from_me = key.get("fromMe", False)
        remote_jid = key.get("remoteJid", "")

        if from_me or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
            log_event(
                logger,
                "INFO",
                "webhook.ignored",
                reason="Mensaje del bot o irrelevante",
                remote_jid=remote_jid,
            )
            return {"status": "ignorado", "reason": "Mensaje del bot o irrelevante"}

        telefono = remote_jid.split("@")[0]

        message_content = msg_data.get("message", {})
        texto = ""

        if "conversation" in message_content:
            texto = message_content["conversation"]
        elif "extendedTextMessage" in message_content:
            texto = message_content["extendedTextMessage"].get("text", "")

        if not texto:
            tipo_mensaje = list(message_content.keys())[0] if message_content else "desconocido"

            if tipo_mensaje in ["audioMessage", "imageMessage", "videoMessage", "documentMessage", "stickerMessage"]:
                respuesta_amable = "¡Uy! 🙈 Por ahora solo puedo procesar mensajes de texto. ¿Podrías escribirme lo que me decías en esa nota o archivo, por favor? ✍️"
                await enviar_mensaje_whatsapp(telefono, respuesta_amable)
                log_event(
                    logger,
                    "INFO",
                    "webhook.non_text_message",
                    telefono=telefono,
                    tipo_mensaje=tipo_mensaje,
                )
                return {"status": "ok", "reason": "Aviso de solo texto enviado"}
            else:
                log_event(
                    logger,
                    "INFO",
                    "webhook.unsupported_message",
                    telefono=telefono,
                    tipo_mensaje=tipo_mensaje,
                )
                return {"status": "ignorado", "reason": f"Tipo de mensaje no soportado: {tipo_mensaje}"}

        # --- SISTEMA DE DESPERTAR AUTOMÁTICO (MODO FAQ ORGÁNICO) ---
        if telefono not in sesiones_activas:
            log_event(
                logger,
                "INFO",
                "faq.session_started",
                telefono=telefono,
            )
            print(f"\n⚠️ El usuario {telefono} inició el chat orgánicamente. Modo FAQ activado.")
            sesiones_activas[telefono] = {
                "es_faq": True,
                "ultima_actividad": time.time(),
                "historial": [
                    {"role": "system", "content": generar_prompt_faq()}
                ]
            }
        else:
            sesiones_activas[telefono]["ultima_actividad"] = time.time()

        # --- DEBOUNCE: encolar en lugar de procesar directo ---
        await encolar_mensaje(telefono, texto)

        log_event(
            logger,
            "INFO",
            "webhook.message_enqueued",
            telefono=telefono,
        )
        return {"status": "ok", "reason": "Mensaje encolado"}

    except Exception as e:
        log_event(
            logger,
            "ERROR",
            "webhook.error",
            error_message=str(e),
            error_type=type(e).__name__,
        )
        return {"status": "error", "message": str(e)}
