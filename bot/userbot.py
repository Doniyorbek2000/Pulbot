"""Dofa MTProto Userbot — O'chib ketadigan rasmlarni saqlash va faqat begona to'lovsiz xabarlarni tozalash."""

import asyncio
import logging
import os
from datetime import datetime, timezone
import aiosqlite
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest, GetContactsRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
logger = logging.getLogger("userbot")

API_ID = 37491042
API_HASH = "de05c1332ab6f269d5a208262cf27910"
SESSION_PATH = "/opt/pulbot/userbot.session"
DB_PATH = "/opt/pulbot/pulbot.db"
BOT_ID = 8557215610

client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

cached_contacts = set()


async def refresh_contacts():
    """Foydalanuvchining Telegram kontaktlarini xotiraga yuklash."""
    global cached_contacts
    try:
        res = await client(GetContactsRequest(hash=0))
        if hasattr(res, 'contacts'):
            cached_contacts = {c.user_id for c in res.contacts}
            logger.info("Kontaktdagilar yangilandi: %s ta kontakt ro'yxatda mavjud.", len(cached_contacts))
    except Exception as e:
        logger.error("Kontaktlarni yuklashda xatolik: %s", e)


async def is_user_permitted(sender, sender_id: int, owner_id: int) -> bool:
    """Foydalanuvchining to'langan ruxsati, kontaktda borligi yoki oq ro'yxatdaligini tekshiradi."""
    is_contact = (
        sender_id in cached_contacts 
        or getattr(sender, 'contact', False) is True
        or getattr(sender, 'mutual_contact', False) is True
    )

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. Inbox sozlamalarini tekshirish
            async with db.execute(
                "SELECT mode, free_for_premium FROM inbox_settings WHERE user_id = ?",
                (owner_id,)
            ) as cursor:
                inbox_row = await cursor.fetchone()
                if inbox_row:
                    mode, free_for_premium = inbox_row
                    # AGAR BOT TO'XTATILGAN (OPEN) BO'LSA — 100% HAMMAGA RUXSAT BERILADI, XABARLAR O'CHIRILMAYDI!
                    if mode == "open":
                        logger.info("Bot to'xtatilgan (open rejim): %s foydalanuvchisiga erkin ruxsat berildi!", sender_id)
                        return True
                    # Kontaktdagilar doimo bepul
                    if is_contact:
                        logger.info("Foydalanuvchi %s kontaktingizda bo'lgani uchun ruxsat berildi!", sender_id)
                        return True
                    # Agar Premium bepul bo'lsa
                    if free_for_premium and getattr(sender, 'premium', False):
                        logger.info("Foydalanuvchi %s Telegram Premium bo'lgani uchun ruxsat berildi!", sender_id)
                        return True
                else:
                    return True

            # 2. Istisnolarda (Oq ro'yxatda) bormi?
            async with db.execute(
                "SELECT id FROM access_rules WHERE owner_id = ? AND target_id = ? AND kind = 'free'",
                (owner_id, sender_id),
            ) as cursor:
                if await cursor.fetchone():
                    logger.info("Foydalanuvchi %s Oq ro'yxatda bo'lgani uchun ruxsat berildi!", sender_id)
                    return True

            # 3. To'langan aktiv ruxsati bormi?
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            async with db.execute(
                "SELECT id, messages_left FROM active_permissions WHERE owner_id = ? AND user_id = ? AND target_type = 'dm_session' AND expires_at > ?",
                (owner_id, sender_id, now_iso),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    perm_id, msgs_left = row
                    if msgs_left is not None:
                        if msgs_left > 0:
                            await db.execute(
                                "UPDATE active_permissions SET messages_left = messages_left - 1 WHERE id = ?",
                                (perm_id,),
                            )
                            await db.commit()
                            return True
                        else:
                            return False
                    return True
    except Exception as e:
        logger.error("DB ruxsat tekshirishda xatolik: %s", e)
        return True  # Xatolik bo'lsa hech qachon xabarni xato o'chirmaymiz!
            
    if is_contact:
        return True
        
    return False


@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def handle_private_message(event):
    """Shaxsiy chatga kelgan har qanday xabar va medialarni ushlab qolish."""
    message = event.message
    sender = await event.get_sender()
    sender_id = event.sender_id
    
    # 1. Hisob egasining o'zi yozgan bo'lsa e'tiborsiz qoldirish
    me = await client.get_me()
    if sender_id == me.id:
        return

    # 2. Botlar va bot chatlariga (shu jumladan Dofa botga) aslo tegmaymiz
    if getattr(sender, 'bot', False) is True or sender_id == BOT_ID or event.chat_id == BOT_ID:
        return

    has_media = message.media is not None
    ttl = getattr(message.media, 'ttl_seconds', None) if has_media else None
    
    sender_name = getattr(sender, 'first_name', '') or "Noma'lum"
    username = getattr(sender, 'username', None)
    user_ref = f"@{username}" if username else f"ID: {sender_id}"
    time_str = datetime.now().strftime("%H:%M:%S, %d.%m.%Y")
    
    # 3. 1-martalik yoki har qanday mediani Saved Messages ga saqlash
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
            temp_dir = "/tmp/dofa_media"
            os.makedirs(temp_dir, exist_ok=True)
            saved_path = await client.download_media(message, file=temp_dir)
            if saved_path and os.path.exists(saved_path):
                await client.send_file(
                    "me",
                    saved_path,
                    caption=caption,
                    parse_mode="html",
                )
                logger.info("Media 'Saqlangan xabarlar' (Saved Messages)ga rasm/video formatida saqlandi! (%s, %s)", sender_id, saved_path)
                try:
                    os.remove(saved_path)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Mediani saqlashda xatolik: %s", e)

    # 4. Faqat to'lov qilinmagan NOTANISH insonlar xabarlarini chatdan o'chirish
    permitted = await is_user_permitted(sender, sender_id, me.id)
    if not permitted:
        logger.info("Foydalanuvchi %s to'lov qilmagan va notanish: xabar chatdan o'chirilmoqda...", sender_id)
        try:
            await message.delete()
            logger.info("To'lanmagan notanish xabar chatdan o'chirildi! (%s)", sender_id)
        except Exception as e:
            logger.error("Xabarni o'chirishda xatolik: %s", e)


async def check_unblock_queue():
    """To'lov qilgan foydalanuvchilarni avtomatik blokdan chiqarish fon vazifasi."""
    while True:
        try:
            if client.is_connected():
                async with aiosqlite.connect(DB_PATH) as db:
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    async with db.execute(
                        """
                        SELECT user_id FROM active_permissions 
                        WHERE target_type = 'dm_session' AND expires_at > ?
                        UNION
                        SELECT target_id as user_id FROM access_rules WHERE kind = 'free'
                        """,
                        (now_iso,),
                    ) as cursor:
                        rows = await cursor.fetchall()
                        for (user_id,) in rows:
                            try:
                                await client(UnblockRequest(id=user_id))
                            except Exception:
                                pass
        except Exception as e:
            logger.error("Unblock tekshiruvida xatolik: %s", e)
        await asyncio.sleep(3)


async def main():
    logger.info("Dofa MTProto Userbot ishga tushmoqda...")
    await client.start()
    me = await client.get_me()
    logger.info("Userbot muvaffaqiyatli ulandi: %s (@%s, ID=%s)", me.first_name, me.username, me.id)
    
    # Kontaktlarni xotiraga yuklash
    await refresh_contacts()
    
    # Unblock fon vazifasini ishga tushirish
    asyncio.create_task(check_unblock_queue())
    
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
