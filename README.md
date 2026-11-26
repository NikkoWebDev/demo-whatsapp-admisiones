# Demo WhatsApp Admisiones 2027 — Colegio Técnico Ciudad del Sol

Asistente virtual automatizado con soporte **Multimodal (Texto, Notas de Voz e Imágenes)** para el proceso de **Admisiones y Matrículas 2027** del **Colegio Técnico Ciudad del Sol** (Sogamoso, Boyacá).

---

## ⚡ Novedades y Configuración Multimodal

1. **Modelo Primario**: **`gemini-4-hydrogen`** (cuenta 1) con parámetros de ultra baja latencia (`thinkingBudget: 0`).
2. **Fallback Automático en Cascada**: Cuenta 2 con **`gemini-2.5-flash`** (o Pro) si el modelo primario presenta saturación (503/429), garantizando 100% de disponibilidad.
3. **Soporte Multimodal Completo**:
   - 🎙️ **Notas de Voz / Audios de WhatsApp** (`audio/ogg` con códec Opus): Baileys descarga el audio, lo envía a Gemini y responde a la consulta hablada.
   - 📸 **Imágenes y Fotos**: Fotografías de documentos de identidad, boletines de notas o certificados son analizados directamente.
   - 💬 **Texto**: Consultas tradicionales por chat.

---

## 📁 Estructura del Proyecto

```text
demo-whatsapp-admisiones/
├── raw_pdfs/                      # Coloca aquí los PDFs oficiales
│   ├── PROSPECTO 2027.pdf
│   ├── Costos_LCS_2027.pdf
│   └── Costos educativos Ciudad de Sol.pdf
├── scripts/
│   └── extract_pdfs.py            # Extrae texto y tablas a Markdown limpio
├── data/
│   ├── conocimiento_2027.md       # Base de conocimiento inyectada al LLM
│   ├── usage.json                 # Contador mensual de mensajes persistido
│   └── leads.jsonl                # Registro de interesados y escalamientos
├── backend/                       # Servidor Python 3.12 / FastAPI
│   ├── main.py                    # Endpoint multimodal /chat, fallback y memoria
│   ├── prompt.py                  # System Prompt estricto anti-alucinación
│   ├── requirements.txt           # Dependencias Python
│   ├── Dockerfile
│   └── .env                       # Llaves de Gemini (Primaria + Fallback)
├── whatsapp-bridge/               # Puente Node.js con Baileys
│   ├── index.js                   # QR, descarga de audios/fotos y reenvío
│   ├── package.json               # Dependencias npm (@whiskeysockets/baileys)
│   ├── Dockerfile
│   └── auth_info/                 # Sesión persistente de WhatsApp (auto-generada)
├── docker-compose.yml             # Despliegue en 1 comando
├── .gitignore
└── README.md
```

---

## 🚀 Guía Rápida de Ejecución Local

### Paso 1: Levantar el Backend (FastAPI)
```bash
# Activar entorno virtual
source .venv/bin/activate

# Iniciar servidor
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Paso 2: Conectar el Puente de WhatsApp (Escanear QR)
En otra terminal:
```bash
cd whatsapp-bridge
npm start
```
Escanea el código QR que se dibuja en la terminal desde WhatsApp (*Dispositivos vinculados*). La sesión se guardará en `auth_info/`.

---

## 🌐 ¿Dónde lo puedo subir? (Guía de Despliegue en Producción)

WhatsApp con Baileys mantiene una **conexión WebSocket persistente en tiempo real 24/7** y requiere guardar la sesión en disco (`auth_info/`). Por este motivo, **plataformas serverless como Vercel o Netlify NO son compatibles**.

A continuación tienes las **3 mejores opciones para subirlo**:

---

### Opción 1 (Recomendada): VPS en la Nube (DigitalOcean / Hetzner / AWS Lightsail / Oracle Cloud)
Es la opción más robusta, económica y profesional.

1. **Proveedores recomendados:**
   - **Hetzner Cloud** (~€3.80 al mes - servidor CX22 con 4GB RAM).
   - **DigitalOcean Droplet** (~$4 o $6 al mes en Basic Droplet).
   - **AWS Lightsail** (~$3.50 al mes).
   - **Oracle Cloud Always Free** (Gratis para siempre en su capa gratuita con Ubuntu).

2. **Cómo desplegar en el VPS con Docker en 2 minutos:**
   Sube tu carpeta al VPS por Git o SCP y corre:
   ```bash
   # En el VPS con Docker instalado:
   docker compose up -d --build
   ```
   Para ver el código QR y vincular el WhatsApp por primera vez:
   ```bash
   docker compose logs -f whatsapp-bridge
   ```
   Escaneas el QR desde tu celular una sola vez y listo. ¡Queda corriendo 24/7 en segundo plano!

---

### Opción 2: Plataformas PaaS con Disco Persistente (Railway o Render)

Si prefieres no administrar un Linux por terminal:

- **Railway.app** (Muy fácil):
  1. Conecta tu repositorio de GitHub.
  2. Crea dos servicios: uno para `backend` y otro para `whatsapp-bridge`.
  3. En `whatsapp-bridge`, añade un **Volume (Persistent Disk)** montado en `/app/auth_info` para que la sesión de WhatsApp no se borre al reiniciar.
  4. Configura las variables de entorno en el panel.
  5. En los logs de despliegue verás el código QR para escanear.

- **Render.com**:
  1. Crea un Web Service (Node.js) con Docker y añade un Persistent Disk.
  2. Costo aproximado: $7/mes por el disco persistente.

---

### Opción 3: Servidor Local o Computadora en el Colegio (Costo $0)

Puedes usar un Mini PC, una Raspberry Pi 4/5 (con Ubuntu Server) o una computadora de oficina en Secretaría del Colegio:

1. Deja el equipo conectado a internet por cable y sin suspensión automática.
2. Levantas los servicios con `docker compose up -d` o con `pm2`:
   ```bash
   # Con PM2 para arranque automático con el sistema
   pm2 start "uvicorn backend.main:app --port 8000" --name "admisiones-backend"
   pm2 start "node whatsapp-bridge/index.js" --name "admisiones-whatsapp"
   pm2 save
   pm2 startup
   ```
3. Esta opción es ideal para pruebas piloto sin ningún gasto mensual en servidores en la nube.

---

## 🧪 Pruebas con `curl`

### Prueba de Texto:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"phone": "573123456789", "message": "Hola, ¿cuánto vale la pensión para sexto?"}'
```

### Prueba con Imagen (Base64):
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "573123456789",
    "message": "¿Qué trámite corresponde a este documento?",
    "media": {
      "mime_type": "image/png",
      "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    }
  }'
```
