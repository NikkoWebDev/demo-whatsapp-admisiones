FROM node:22-slim

WORKDIR /app

# Copiar package y archivo npmrc para habilitar dependencias git de Baileys
COPY whatsapp-bridge/package*.json ./
COPY whatsapp-bridge/.npmrc ./

RUN npm install --omit=dev

COPY whatsapp-bridge/index.js ./

# Directorio de persistencia de credenciales de sesión
VOLUME ["/app/auth_info"]

CMD ["node", "index.js"]
