/**
 * whatsapp-bridge/index.js
 * Puente de WhatsApp con Node.js y Baileys (QR code) conectado al backend FastAPI.
 * Soporte Multimodal: Texto, Notas de Voz (Audio) e Imágenes / Documentos.
 */

import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import dotenv from 'dotenv';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage
} from '@whiskeysockets/baileys';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Cargar variables de entorno
dotenv.config();

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000/chat';
const AUTH_DIR = path.join(__dirname, 'auth_info');

// Logger silencioso para Baileys
const logger = pino({ level: process.env.LOG_LEVEL || 'error' });

// Asegurar directorio de autenticación
if (!fs.existsSync(AUTH_DIR)) {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
}

console.log('============================================================');
console.log(' Puente WhatsApp Multimodal - Admisiones Ciudad del Sol');
console.log(` Backend FastAPI URL: ${BACKEND_URL}`);
console.log(` Soporte: Texto | Notas de Voz (Audio) | Imágenes`);
console.log(` Directorio de sesión: ${AUTH_DIR}`);
console.log('============================================================\n');

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion().catch(() => ({
    version: [2, 3000, 1015901307],
    isLatest: false
  }));

  const sock = makeWASocket({
    version,
    logger,
    printQRInTerminal: false,
    auth: state,
    generateHighQualityLinkPreview: false,
    syncFullHistory: false
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n[QR RECIBIDO] Escanea el siguiente código QR con WhatsApp en tu celular:');
      console.log('(WhatsApp > Ajustes / Menú > Dispositivos vinculados > Vincular un dispositivo)\n');
      qrcode.generate(qr, { small: true });
      console.log('\nEsperando escaneo...');
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const reason = lastDisconnect?.error?.message || 'Desconocido';
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

      console.log(`\n[CONEXIÓN CERRADA] Código: ${statusCode} | Motivo: ${reason}`);

      if (shouldReconnect) {
        console.log('[RECONECTANDO] Intentando reconectar al servicio de WhatsApp en 3 segundos...');
        setTimeout(() => connectToWhatsApp(), 3000);
      } else {
        console.log('[SESIÓN FINALIZADA] La sesión fue cerrada desde el dispositivo móvil.');
        console.log('Para volver a vincular, elimina la carpeta auth_info/ y reinicia el servicio.');
      }
    } else if (connection === 'open') {
      console.log('\n============================================================');
      console.log('[CONEXIÓN EXITOSA] WhatsApp en línea y listo para recibir chats.');
      console.log('============================================================\n');
    }
  });

  // Escuchar mensajes entrantes
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      try {
        if (!msg.message) continue;
        if (msg.key.fromMe) continue;

        const remoteJid = msg.key.remoteJid;
        if (!remoteJid) continue;

        // Ignorar grupos y difusiones
        if (remoteJid.endsWith('@g.us') || remoteJid.includes('broadcast')) {
          continue;
        }

        const phone = remoteJid.replace('@s.whatsapp.net', '');
        const pushName = msg.pushName || 'Usuario';

        // Detectar tipo de contenido: Texto, Audio o Imagen
        let userText = '';
        let mediaPayload = null;
        let mediaTypeLabel = 'Texto';

        if (msg.message.audioMessage) {
          mediaTypeLabel = 'Nota de Voz / Audio';
          const audioMsg = msg.message.audioMessage;
          const mimeType = audioMsg.mimetype || 'audio/ogg';
          try {
            console.log(`[DESCARGANDO AUDIO] De ${pushName} (${mimeType})...`);
            const buffer = await downloadMediaMessage(
              msg,
              'buffer',
              {},
              { logger, reuploadRequest: sock.updateMediaMessage }
            );
            mediaPayload = {
              mime_type: mimeType,
              data: buffer.toString('base64')
            };
          } catch (e) {
            console.error('[ERROR] Descargando audio:', e.message);
          }
        } else if (msg.message.imageMessage) {
          mediaTypeLabel = 'Imagen / Foto';
          const imgMsg = msg.message.imageMessage;
          const mimeType = imgMsg.mimetype || 'image/jpeg';
          userText = (imgMsg.caption || '').trim();
          try {
            console.log(`[DESCARGANDO IMAGEN] De ${pushName} (${mimeType})...`);
            const buffer = await downloadMediaMessage(
              msg,
              'buffer',
              {},
              { logger, reuploadRequest: sock.updateMediaMessage }
            );
            mediaPayload = {
              mime_type: mimeType,
              data: buffer.toString('base64')
            };
          } catch (e) {
            console.error('[ERROR] Descargando imagen:', e.message);
          }
        } else {
          // Texto estándar
          userText = (
            msg.message.conversation ||
            msg.message.extendedTextMessage?.text ||
            ''
          ).trim();
        }

        // Si no hay texto ni multimedia, ignorar
        if (!userText && !mediaPayload) continue;

        console.log(`\n------------------------------------------------------------`);
        console.log(`[MENSAJE ENTRANTE] De: ${pushName} (+${phone}) | Tipo: ${mediaTypeLabel}`);
        if (userText) console.log(` Texto: "${userText}"`);

        // Enviar presencia "escribiendo..." (o "grabando audio...")
        const presence = mediaTypeLabel.includes('Audio') ? 'recording' : 'composing';
        await sock.sendPresenceUpdate(presence, remoteJid);
        await new Promise((resolve) => setTimeout(resolve, 1000));

        // Enviar al backend FastAPI
        let replyText = '';
        const startTime = Date.now();

        try {
          const bodyPayload = {
            phone,
            message: userText,
            media: mediaPayload
          };

          const response = await fetch(BACKEND_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bodyPayload),
            signal: AbortSignal.timeout(20000)
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status} ${response.statusText}`);
          }

          const data = await response.json();
          replyText = data.reply || '';
          const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);

          console.log(` [LLM RESPUESTA] Modelo: ${data.model_used} (${elapsed}s)`);
          if (data.lead_saved) console.log(` [LEAD] Registrado en leads.jsonl`);
          if (data.quota_exceeded) console.log(` [CUOTA] Límite mensual alcanzado.`);
        } catch (fetchError) {
          console.error(`[ERROR BACKEND] ${BACKEND_URL}: ${fetchError.message}`);
          replyText =
            'Hola, en este momento nuestro sistema de admisiones se encuentra actualizando información. ' +
            'Por favor comunícate directamente con Secretaría Académica al +57 312 345 6789 o al (608) 770-1234. ¡Gracias!';
        }

        // Detener presencia y responder
        await sock.sendPresenceUpdate('paused', remoteJid);

        if (replyText) {
          await sock.sendMessage(remoteJid, { text: replyText });
          console.log(`[RESPUESTA ENVIADA] A: +${phone}`);
          console.log(` Texto: "${replyText.substring(0, 95)}..."`);
        }
        console.log(`------------------------------------------------------------`);
      } catch (err) {
        console.error('[ERROR MENSAJE]:', err);
      }
    }
  });
}

// Iniciar conexión
connectToWhatsApp().catch((err) => {
  console.error('[ERROR FATAL]:', err);
});
