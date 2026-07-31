"""
Alertas mínimas para no depender de mirar journalctl a mano. Usa un bot
de Telegram (gratis, sin límites prácticos para este volumen) — si no lo
configuras, simplemente no envía nada y todo sigue funcionando igual.

Cómo crear el bot (2 minutos, gratis):
1. Habla con @BotFather en Telegram, /newbot, sigue los pasos -> te da un token.
2. Habla con tu bot nuevo (cualquier mensaje) para "activarlo" contigo.
3. Visita https://api.telegram.org/bot<TU_TOKEN>/getUpdates y copia el
   "chat":{"id": ...} de la respuesta -> ese es tu TELEGRAM_CHAT_ID.
4. En el .env:
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
"""
import os
import logging
import requests

log = logging.getLogger("notify")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_alert(message: str) -> None:
    """Notificaciones desactivadas."""
    pass
