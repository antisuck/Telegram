import asyncio
import logging
import os
import sqlite3
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

DELETE_AFTER = 30   # через сколько секунд удалять видео у получателя
EDIT_STEP = 5       # шаг (в секундах) обновления счётчика в подписи

DB_PATH = "files.db"

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN. Создайте файл .env на основе .env.example")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------------------- База данных ----------------------

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            uid TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            caption TEXT,
            added_by INTEGER,
            downloads INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def save_file(uid: str, file_id: str, caption: str | None, added_by: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO files (uid, file_id, caption, added_by) VALUES (?, ?, ?, ?)",
        (uid, file_id, caption, added_by),
    )
    conn.commit()
    conn.close()


def get_file(uid: str):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT file_id, caption FROM files WHERE uid = ?", (uid,)
    ).fetchone()
    conn.close()
    return row


def increment_downloads(uid: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE files SET downloads = downloads + 1 WHERE uid = ?", (uid,))
    conn.commit()
    cur = conn.execute("SELECT downloads FROM files WHERE uid = ?", (uid,))
    count = cur.fetchone()[0]
    conn.close()
    return count


# ---------------------- Хендлеры ----------------------

@dp.message(CommandStart())
async def start_handler(message: Message, command: CommandObject) -> None:
    # /start <uid> — переход по ссылке-приглашению
    if command.args:
        await send_video_by_link(message, command.args)
        return

    await message.answer(
        "👋 Привет! Это файлообменник для видео.\n\n"
        "• Админы отправляют боту видео — бот сохраняет его и выдаёт уникальную ссылку.\n"
        "• Любой человек по этой ссылке получает видео, но оно "
        f"будет автоматически удалено через {DELETE_AFTER} секунд."
    )


async def send_video_by_link(message: Message, uid: str) -> None:
    row = get_file(uid)
    if not row:
        await message.answer("❌ Файл не найден или ссылка недействительна.")
        return

    file_id, caption = row
    downloads = increment_downloads(uid)

    base_caption = caption or ""
    text = (
        f"{base_caption}\n\n" if base_caption else ""
    ) + f"⏳ Видео будет удалено через {DELETE_AFTER} сек.\n📥 Скачиваний по этой ссылке: {downloads}"

    sent = await message.answer_video(file_id, caption=text)

    asyncio.create_task(
        countdown_and_delete(message.chat.id, sent.message_id, base_caption, downloads)
    )


async def countdown_and_delete(
    chat_id: int, message_id: int, base_caption: str, downloads: int
) -> None:
    remaining = DELETE_AFTER

    while remaining > 0:
        await asyncio.sleep(EDIT_STEP)
        remaining -= EDIT_STEP

        if remaining <= 0:
            break

        new_text = (
            f"{base_caption}\n\n" if base_caption else ""
        ) + f"⏳ Видео будет удалено через {remaining} сек.\n📥 Скачиваний по этой ссылке: {downloads}"

        try:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id, caption=new_text
            )
        except Exception:
            # сообщение могли уже удалить вручную или текст совпал — игнорируем
            pass

    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


@dp.message(F.video)
async def video_handler(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Загружать видео могут только администраторы.")
        return

    file_id = message.video.file_id
    uid = uuid.uuid4().hex[:10]
    save_file(uid, file_id, message.caption, message.from_user.id)

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={uid}"

    await message.answer(
        "✅ Видео сохранено!\n\n"
        f"🔗 Ссылка для скачивания:\n{link}\n\n"
        f"⚠️ После открытия по ссылке видео автоматически удалится у получателя через {DELETE_AFTER} сек."
    )


# ---------------------- Запуск ----------------------

async def main() -> None:
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
