"""
main.py: Servidor FastAPI para el asistente de Admisiones 2027 del Colegio Técnico Ciudad del Sol.
Arquitectura Multi-Proveedor con Fallback Inteligente:
- Texto ultrarrápido (<200ms): Groq (openai/gpt-oss-120b / Llama).
- Multimodal (Audios, Notas de voz e Imágenes): Gemini 4 Hydrogen / 2.5 Flash.
- Conmutación automática de Fallback si cualquiera de los proveedores experimenta congestión.
- Memoria conversacional en RAM (6 turnos, 30 min expiración).
- Control de cuota mensual y captura de leads.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from backend.prompt import get_system_prompt

# Cargar variables de entorno
load_dotenv()

# Rutas del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

KNOWLEDGE_PATH = Path(os.getenv("KNOWLEDGE_PATH", str(DATA_DIR / "conocimiento_2027.md")))
USAGE_PATH = Path(os.getenv("USAGE_PATH", str(DATA_DIR / "usage.json")))
LEADS_PATH = Path(os.getenv("LEADS_PATH", str(DATA_DIR / "leads.jsonl")))

# Configuración de Proveedores
PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", "groq").lower()

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Gemini
GEMINI_PRIMARY_KEY = os.getenv("GEMINI_PRIMARY_KEY", "")
GEMINI_PRIMARY_MODEL = os.getenv("GEMINI_PRIMARY_MODEL", "gemini-4-hydrogen")
GEMINI_FALLBACK_KEY = os.getenv("GEMINI_FALLBACK_KEY", "")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

# Parámetros de generación
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "500"))

# Control de cuota mensual
MAX_MESSAGES_MONTH = int(os.getenv("MAX_MESSAGES_MONTH", "1000"))

# Estado en memoria
KNOWLEDGE_CONTENT: str = ""
SYSTEM_PROMPT: str = ""

# Memoria conversacional en RAM: phone -> list of {role, content, timestamp}
CONVERSATIONS: Dict[str, List[dict]] = {}
MEMORY_EXPIRATION_SECONDS = 30 * 60  # 30 minutos
MAX_TURNS_MEMORY = 6  # 6 turnos (12 mensajes)


# Modelos Pydantic
class MediaPayload(BaseModel):
    mime_type: str = Field(..., description="Tipo MIME del archivo (ej: audio/ogg, image/jpeg, image/png)")
    data: str = Field(..., description="Contenido binario codificado en Base64")


class ChatRequest(BaseModel):
    phone: str = Field(..., description="Número de teléfono o identificador del remitente en WhatsApp")
    message: Optional[str] = Field(default="", description="Texto del mensaje o pie de foto")
    media: Optional[MediaPayload] = Field(default=None, description="Audio o imagen adjunta en base64")


class ChatResponse(BaseModel):
    reply: str
    lead_saved: bool = False
    quota_exceeded: bool = False
    used_quota: int = 0
    max_quota: int = MAX_MESSAGES_MONTH
    model_used: str = ""


class ReloadResponse(BaseModel):
    status: str
    bytes_loaded: int
    updated_at: str


# ==============================================================================
# GESTIÓN DE CONOCIMIENTO (MARKDOWN)
# ==============================================================================
def load_knowledge_base() -> str:
    global KNOWLEDGE_CONTENT, SYSTEM_PROMPT
    if not KNOWLEDGE_PATH.exists():
        print(f"[!] Archivo de conocimiento no encontrado en {KNOWLEDGE_PATH}. Creando plantilla.")
        KNOWLEDGE_PATH.write_text("# PROSPECTO 2027.pdf\nInformación oficial en proceso.", encoding="utf-8")

    content = KNOWLEDGE_PATH.read_text(encoding="utf-8").strip()
    KNOWLEDGE_CONTENT = content
    SYSTEM_PROMPT = get_system_prompt(content)
    print(f"[OK] Base de conocimiento cargada ({len(content.encode('utf-8'))} bytes) desde: {KNOWLEDGE_PATH}")
    return content


# ==============================================================================
# CONTROL DE CUOTA MENSUAL
# ==============================================================================
def get_current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_usage_data() -> dict:
    current_month = get_current_month_key()
    if USAGE_PATH.exists():
        try:
            with open(USAGE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("month") == current_month:
                    return data
        except Exception as e:
            print(f"[!] Error leyendo {USAGE_PATH}: {e}. Reiniciando contador.")

    initial_data = {
        "month": current_month,
        "count": 0,
        "max_messages": MAX_MESSAGES_MONTH,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    save_usage_data(initial_data)
    return initial_data


def save_usage_data(data: dict):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_and_increment_quota() -> tuple[bool, int]:
    data = load_usage_data()
    count = data.get("count", 0)

    if count >= MAX_MESSAGES_MONTH:
        print(f"[CUOTA EXCEDIDA] ({count}/{MAX_MESSAGES_MONTH}). Bloqueando llamadas al LLM.")
        return False, count

    count += 1
    data["count"] = count
    save_usage_data(data)

    threshold_80 = int(MAX_MESSAGES_MONTH * 0.8)
    if count == threshold_80 or (count > threshold_80 and count % 50 == 0):
        print(f"[ALERTA CUOTA] Atención: Consumo al 80%+ ({count}/{MAX_MESSAGES_MONTH} mensajes).")

    return True, count


# ==============================================================================
# MEMORIA CONVERSACIONAL EN RAM
# ==============================================================================
def get_conversation_history(phone: str) -> List[dict]:
    now = time.time()
    if phone in CONVERSATIONS:
        history = CONVERSATIONS[phone]
        if history:
            last_timestamp = history[-1].get("timestamp", 0)
            if now - last_timestamp > MEMORY_EXPIRATION_SECONDS:
                print(f"[MEMORIA] Sesión expirada para {phone} (>30 min).")
                CONVERSATIONS[phone] = []
                return []
        return CONVERSATIONS[phone]
    return []


def update_conversation_history(phone: str, user_text: str, assistant_reply: str):
    now = time.time()
    history = get_conversation_history(phone)
    history.append({"role": "user", "content": user_text, "timestamp": now})
    history.append({"role": "assistant", "content": assistant_reply, "timestamp": now})

    if len(history) > MAX_TURNS_MEMORY * 2:
        history = history[-(MAX_TURNS_MEMORY * 2):]

    CONVERSATIONS[phone] = history


# ==============================================================================
# CLIENTE GROQ (ULTRA RÁPIDO PARA TEXTO)
# ==============================================================================
async def query_groq_api(messages: list) -> str:
    """Envía solicitud a Groq vía API compatible con OpenAI para respuesta en menos de 200ms."""
    url = f"{GROQ_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"Error Groq HTTP {response.status_code}: {response.text[:200]}")

        data = response.json()
        reply = data["choices"][0]["message"]["content"]
        return reply.strip()


# ==============================================================================
# CLIENTE GEMINI (MULTIMODAL PARA AUDIO, IMÁGENES Y TEXTO)
# ==============================================================================
async def query_gemini_api(model: str, api_key: str, contents: list, system_prompt: str) -> str:
    """Envía solicitud a Gemini con soporte de inlineData (Base64) para audio e imágenes."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": LLM_TEMPERATURE,
            "maxOutputTokens": LLM_MAX_TOKENS,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"Error Gemini HTTP {response.status_code}: {response.text[:200]}")

        res_json = response.json()
        candidates = res_json.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini no retornó candidatos de respuesta.")

        parts = candidates[0].get("content", {}).get("parts", [])
        reply_texts = [p.get("text", "") for p in parts if "text" in p]
        if not reply_texts:
            raise RuntimeError("Sin texto válido en respuesta de Gemini.")

        return "".join(reply_texts).strip()


# ==============================================================================
# ENRUTADOR CON FALLBACK RESILIENTE
# ==============================================================================
async def execute_smart_chat(phone: str, user_text: str, media: Optional[MediaPayload]) -> tuple[str, str]:
    """
    Estrategia de resolución inteligente:
    1. Si hay media (audio/imagen): Envía a Gemini multimodal (con fallback secundario).
    2. Si es solo texto:
       - Si PRIMARY_PROVIDER == 'groq': Intenta Groq primero (<0.2s); si falla, salta a Gemini.
       - Si PRIMARY_PROVIDER == 'gemini': Intenta Gemini primero; si falla, salta a Groq.
    """
    history = get_conversation_history(phone)
    has_media = media is not None

    # CASO 1: MULTIMODAL (Nota de voz o Foto) -> Canal Gemini
    if has_media:
        gemini_contents = []
        for h in history:
            role = "user" if h["role"] == "user" else "model"
            gemini_contents.append({"role": role, "parts": [{"text": h["content"]}]})

        current_parts = []
        if user_text:
            current_parts.append({"text": user_text})
        elif "audio" in media.mime_type:
            current_parts.append({"text": "Nota de voz recibida del acudiente sobre admisiones 2027:"})
        elif "image" in media.mime_type:
            current_parts.append({"text": "Imagen recibida del acudiente referente al proceso de matrícula:"})

        clean_mime = media.mime_type.split(";")[0].strip()
        current_parts.append({
            "inlineData": {
                "mimeType": clean_mime,
                "data": media.data
            }
        })
        gemini_contents.append({"role": "user", "parts": current_parts})

        # 1. Intentar Gemini Primario (Hydrogen)
        if GEMINI_PRIMARY_KEY:
            try:
                t0 = time.time()
                reply = await query_gemini_api(GEMINI_PRIMARY_MODEL, GEMINI_PRIMARY_KEY, gemini_contents, SYSTEM_PROMPT)
                print(f"[MULTIMODAL] Respondido por {GEMINI_PRIMARY_MODEL} en {time.time() - t0:.2f}s")
                return reply, GEMINI_PRIMARY_MODEL
            except Exception as e:
                print(f"[FALLBACK MULTIMODAL] {GEMINI_PRIMARY_MODEL} falló: {e}")

        # 2. Fallback Gemini Flash
        if GEMINI_FALLBACK_KEY:
            try:
                t0 = time.time()
                reply = await query_gemini_api(GEMINI_FALLBACK_MODEL, GEMINI_FALLBACK_KEY, gemini_contents, SYSTEM_PROMPT)
                print(f"[MULTIMODAL] Respondido por {GEMINI_FALLBACK_MODEL} (Fallback) en {time.time() - t0:.2f}s")
                return reply, f"{GEMINI_FALLBACK_MODEL} (Fallback)"
            except Exception as e:
                print(f"[ERROR MULTIMODAL] Fallback también falló: {e}")

    # CASO 2: TEXTO (Enrutamiento por preferencia y conmutación de fallo)
    groq_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        groq_messages.append({"role": h["role"], "content": h["content"]})
    groq_messages.append({"role": "user", "content": user_text})

    gemini_contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        gemini_contents.append({"role": role, "parts": [{"text": h["content"]}]})
    gemini_contents.append({"role": "user", "parts": [{"text": user_text}]})

    # Si Groq es el principal (Recomendado para evitar trabas y obtener <200ms)
    if PRIMARY_PROVIDER == "groq" and GROQ_API_KEY:
        try:
            t0 = time.time()
            reply = await query_groq_api(groq_messages)
            elapsed = time.time() - t0
            print(f"[TEXTO] Respondido por Groq ({GROQ_MODEL}) en {elapsed:.2f}s")
            return reply, f"Groq ({GROQ_MODEL})"
        except Exception as groq_err:
            print(f"[FALLBACK A GEMINI] Groq falló ({groq_err}). Activando Gemini...")

    # Si Groq falló o Gemini es el principal
    if GEMINI_PRIMARY_KEY:
        try:
            t0 = time.time()
            reply = await query_gemini_api(GEMINI_PRIMARY_MODEL, GEMINI_PRIMARY_KEY, gemini_contents, SYSTEM_PROMPT)
            print(f"[TEXTO] Respondido por Gemini ({GEMINI_PRIMARY_MODEL}) en {time.time() - t0:.2f}s")
            return reply, GEMINI_PRIMARY_MODEL
        except Exception as gem_err:
            print(f"[FALLBACK GEMINI 2] Primario no respondió ({gem_err}). Probando fallback...")

    # Fallback secundario de Gemini
    if GEMINI_FALLBACK_KEY:
        try:
            t0 = time.time()
            reply = await query_gemini_api(GEMINI_FALLBACK_MODEL, GEMINI_FALLBACK_KEY, gemini_contents, SYSTEM_PROMPT)
            print(f"[TEXTO] Respondido por Gemini Fallback ({GEMINI_FALLBACK_MODEL}) en {time.time() - t0:.2f}s")
            return reply, f"{GEMINI_FALLBACK_MODEL} (Fallback)"
        except Exception as e:
            print(f"[ERROR CRÍTICO] Ambos fallaron: {e}")

    # Si Groq no era el principal pero está configurado, probarlo al final
    if PRIMARY_PROVIDER != "groq" and GROQ_API_KEY:
        try:
            reply = await query_groq_api(groq_messages)
            return reply, f"Groq ({GROQ_MODEL})"
        except Exception:
            pass

    # Mensaje de contingencia
    return (
        "Hola, en este momento nuestro canal de admisiones presenta alta congestión en los servidores. "
        "Por favor comunícate directamente con Secretaría Académica al +57 312 345 6789 (https://wa.me/573123456789) o al correo "
        "colegio.ciudad1971@gmail.com. ¡Con gusto te atenderemos!"
    ), "offline-fallback"


# ==============================================================================
# DETECCIÓN DE LEADS
# ==============================================================================
LEAD_KEYWORDS = [
    "cupo", "cupos", "inscrip", "matrícul", "aspirante", "admi", "nuevo",
    "preescolar", "primaria", "secundaria", "bachillerato", "técnico", "interesad"
]
ESCALATION_KEYWORDS = [
    "secretar", "asesor", "contacto", "teléfono", "persona", "humano",
    "descuento", "beca", "convenio", "queja", "reclamo", "directivo"
]


def detect_and_save_lead(phone: str, user_message: str, assistant_reply: str, has_media: bool) -> bool:
    user_msg_lower = user_message.lower()
    reply_lower = assistant_reply.lower()

    is_lead = any(kw in user_msg_lower for kw in LEAD_KEYWORDS) or has_media
    is_escalation = any(kw in user_msg_lower for kw in ESCALATION_KEYWORDS) or "secretaría" in reply_lower

    if is_lead or is_escalation:
        lead_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phone": phone,
            "is_lead": is_lead,
            "is_escalation": is_escalation,
            "has_media": has_media,
            "message": user_message,
            "reply": assistant_reply
        }
        try:
            with open(LEADS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(lead_entry, ensure_ascii=False) + "\n")
            print(f"[LEAD REGISTRADO] Tel: {phone} | Tipo: {'Lead Cupo/Media' if is_lead else 'Escalamiento'} -> {LEADS_PATH.name}")
            return True
        except Exception as e:
            print(f"[ERROR] No se pudo guardar lead en {LEADS_PATH}: {e}")

    return False


# ==============================================================================
# APLICACIÓN FASTAPI
# ==============================================================================
app = FastAPI(
    title="Asistente WhatsApp Admisiones 2027 (Multi-Proveedor)",
    description="Backend con Groq ultra-rápido, Gemini multimodal y Fallback inteligente",
    version="2.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print(" Asistente Admisiones 2027 - Colegio Técnico Ciudad del Sol")
    print(f" Proveedor Principal Texto: {PRIMARY_PROVIDER.upper()} ({GROQ_MODEL if PRIMARY_PROVIDER == 'groq' else GEMINI_PRIMARY_MODEL})")
    print(f" Proveedor Multimodal:      GEMINI ({GEMINI_PRIMARY_MODEL} / {GEMINI_FALLBACK_MODEL})")
    print(f" Cuota Máxima Mensual:      {MAX_MESSAGES_MONTH} mensajes")
    load_knowledge_base()
    usage = load_usage_data()
    print(f" Consumo actual mes ({usage['month']}): {usage['count']}/{MAX_MESSAGES_MONTH}")
    print("=" * 60)


@app.get("/")
def read_root():
    return {
        "institution": "Colegio Técnico Ciudad del Sol - Sogamoso, Boyacá",
        "service": "Asistente Virtual Admisiones 2027",
        "status": "online",
        "primary_provider": PRIMARY_PROVIDER,
        "models": {
            "text": GROQ_MODEL if PRIMARY_PROVIDER == "groq" else GEMINI_PRIMARY_MODEL,
            "multimodal": GEMINI_PRIMARY_MODEL,
            "fallback": GEMINI_FALLBACK_MODEL
        }
    }


@app.get("/health")
def health_check():
    usage = load_usage_data()
    return {
        "status": "healthy",
        "knowledge_loaded": bool(KNOWLEDGE_CONTENT),
        "knowledge_size_bytes": len(KNOWLEDGE_CONTENT.encode("utf-8")),
        "monthly_usage": f"{usage['count']}/{MAX_MESSAGES_MONTH}",
        "active_memory_sessions": len(CONVERSATIONS),
        "primary_provider": PRIMARY_PROVIDER
    }


@app.get("/usage")
def get_usage():
    data = load_usage_data()
    return {
        "month": data.get("month"),
        "count": data.get("count", 0),
        "max_messages": MAX_MESSAGES_MONTH,
        "percentage_used": round((data.get("count", 0) / MAX_MESSAGES_MONTH) * 100, 2),
        "last_updated": data.get("last_updated")
    }


@app.post("/reload", response_model=ReloadResponse)
def reload_knowledge():
    content = load_knowledge_base()
    return ReloadResponse(
        status="reloaded successfully",
        bytes_loaded=len(content.encode("utf-8")),
        updated_at=datetime.now(timezone.utc).isoformat()
    )


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    clean_phone = req.phone.strip()
    user_msg = (req.message or "").strip()
    has_media = req.media is not None

    if not user_msg and not has_media:
        raise HTTPException(status_code=400, detail="Debe enviar texto, nota de voz o imagen.")

    # 1. Control de cuota mensual
    allowed, current_count = check_and_increment_quota()
    if not allowed:
        fallback_quota_reply = (
            "Estimado acudiente, en este momento hemos alcanzado el límite de atención automatizada mensual por este canal. "
            "Por favor comuníquese directamente con Secretaría Académica al teléfono +57 (608) 770-1234 o al WhatsApp "
            "+57 312 345 6789 para recibir atención personalizada. ¡Muchas gracias por su comprensión!"
        )
        return ChatResponse(
            reply=fallback_quota_reply,
            lead_saved=False,
            quota_exceeded=True,
            used_quota=current_count,
            max_quota=MAX_MESSAGES_MONTH,
            model_used="quota_exceeded"
        )

    # 2. Enrutador Inteligente con Fallback
    reply_text, model_used = await execute_smart_chat(clean_phone, user_msg, req.media)

    # 3. Actualizar memoria en RAM
    summary_user_msg = user_msg or (f"[{req.media.mime_type} recibido]" if has_media else "")
    update_conversation_history(clean_phone, summary_user_msg, reply_text)

    # 4. Registrar Lead
    lead_registered = detect_and_save_lead(clean_phone, summary_user_msg, reply_text, has_media)

    return ChatResponse(
        reply=reply_text,
        lead_saved=lead_registered,
        quota_exceeded=False,
        used_quota=current_count,
        max_quota=MAX_MESSAGES_MONTH,
        model_used=model_used
    )
