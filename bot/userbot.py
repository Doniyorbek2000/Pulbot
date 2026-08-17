"""Dofa MTProto Userbot — O'chib ketadigan (taymerli) rasmlar va medialarni avtomatik saqlash."""

import asyncio
import logging
import os
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
logger = logging.getLogger("userbot")

API_ID = 37491042
API_HASH = "de05c1332ab6f269d5a208262cf27910"
SESSION_PATH = "/opt/pulbot/userbot.session"

client = TelegramClient(SESSION_PATH, API_ID, API_HASH)


@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def handle_private_message(event):
    """Shaxsiy chatga kelgan har qanday xabar va medialarni ushlab qolish."""
    message = event.message
    sender = await event.get_sender()
    sender_id = event.sender_id
    
    # Hisob egasining o'zi yozgan bo'lsa e'tiborsiz qoldirish
    me = await client.get_me()
    if sender_id == me.id:
        return

    has_media = message.media is not None
    ttl = getattr(message.media, 'ttl_seconds', None) if has_media else None
    
    sender_name = getattr(sender, 'first_name', '') or "Noma'lum"
    username = getattr(sender, 'username', None)
    user_ref = f"@{username}" if username else f"ID: {sender_id}"
    time_str = datetime.now().strftime("%H:%M:%S, %d.%m.%Y")
    
    if has_media:
        ttl_label = f" (⏱ {ttl} soniyalik 1 martalik o'chib ketadigan rasm/video)" if ttl else ""
        caption = (
            f"📸 <b>Saqlangan media{ttl_label}!</b>\n\n"
            f"👤 <b>Kimdan:</b> {sender_name} ({user_ref})\n"
            f"⏱ <b>Vaqt:</b> {time_str}\n"
        )
        if message.text:
            caption += f"📝 <b>Matn:</b> {message.text}\n"
            
        logger.info("Media yuklab olinmoqda (kimdan: %s, ttl=%s)...", sender_id, ttl)
        try:
            downloaded = await client.download_media(message, file=bytes)
            if downloaded:
                await client.send_file(
                    "me",
                    downloaded,
                    caption=caption,
                    parse_mode="html",
                )
                logger.info("Media 'Saqlangan xabarlar' (Saved Messages)ga muvaffaqiyatli saqlandi! (%s)", sender_id)
        except Exception as e:
            logger.error("Mediani saqlashda xatolik: %s", e)


async def main():
    logger.info("Dofa MTProto Userbot ishga tushmoqda...")
    await client.start()
    me = await client.get_me()
    logger.info("Userbot muvaffaqiyatli ulandi: %s (@%s, ID=%s)", me.first_name, me.username, me.id)
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
