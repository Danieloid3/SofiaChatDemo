import os
import time
import asyncio
from fastapi import FastAPI, Request
from models import SolicitudChat
from ai_agent import procesar_mensaje, generar_prompt_chat, generar_prompt_faq, sesiones_activas
from whatsapp_utils import enviar_mensaje_whatsapp
from dotenv import load_dotenv

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
    resultado = await procesar_mensaje(telefono, texto)

    print("\n" + "=" * 40)
    print(f"📩 MENSAJE DEL USUARIO ({telefono}): {texto}")
    print(f"🤖 DECISIÓN DE LA IA:")
    print(resultado.model_dump_json(indent=2))
    print("=" * 40 + "\n")

    await enviar_mensaje_whatsapp(telefono, resultado.respuesta_ia_para_usuario)

    if resultado.estado_conversacion == "FINALIZADA":
        print(f"✅ CHAT FINALIZADO con {telefono}. Borrando sesión de memoria.")
        if telefono in sesiones_activas:
            del sesiones_activas[telefono]


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post("/solicitar-chat")
async def iniciar_chat(solicitud: SolicitudChat):
    """Recibe la orden para iniciar el chat de demostración con un inscrito a la presentación."""
    telefono = solicitud.phone.replace("+", "").replace(" ", "")

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
            f"(del clan Hamilton). \n\n¡Qué emoción tenerte en nuestra demo interactiva! Además, aprovecho para felicitarte "
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

        evento = body.get("event", "")
        if evento not in ["messages.upsert", "MESSAGES_UPSERT"]:
            return {"status": "ignorado", "reason": "No es un evento de mensaje"}

        data = body.get("data", {})
        msg_data = data.get("message", data) if "key" not in data else data

        key = msg_data.get("key", {})
        from_me = key.get("fromMe", False)
        remote_jid = key.get("remoteJid", "")

        if from_me or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
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
                return {"status": "ok", "reason": "Aviso de solo texto enviado"}
            else:
                return {"status": "ignorado", "reason": f"Tipo de mensaje no soportado: {tipo_mensaje}"}

        # --- SISTEMA DE DESPERTAR AUTOMÁTICO (MODO FAQ ORGÁNICO) ---
        if telefono not in sesiones_activas:
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

        return {"status": "ok", "reason": "Mensaje encolado"}

    except Exception as e:
        print(f"❌ Error procesando mensaje del webhook: {e}")
        return {"status": "error", "message": str(e)}
