import asyncio
import html
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from io import BytesIO
import socket
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8834602809:AAGv_Hkwm2JL1edtF4hH_mKhCUskc8HV42Y")
OWNER_ID = int(os.environ.get("OWNER_ID", "7752398574"))
MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://example.com/app")

CAPTURE_ON_REPLY = True
SEND_DELETED_MEDIA = True
SAVE_TRIGGERS = {"ема"}

# Создаем бота БЕЗ кастомной сессии на глобальном уровне
# IP_FAMILY: auto (по умолчанию) | ipv4 | ipv6
_FAMILIES = {"auto": socket.AF_UNSPEC, "ipv4": socket.AF_INET, "ipv6": socket.AF_INET6}
IP_FAMILY = _FAMILIES.get(os.getenv("IP_FAMILY", "auto").lower(), socket.AF_UNSPEC)


class FamilySession(AiohttpSession):
    """aiogram сам создаёт aiohttp-сессию; просто задаём семейство IP для TCPConnector."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connector_init["family"] = IP_FAMILY


bot = Bot(
    BOT_TOKEN,
    session=FamilySession(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# ---------------- БАЗА ----------------
db = sqlite3.connect("saved.db", check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS messages(
    conn_id TEXT, chat_id INTEGER, msg_id INTEGER,
    user_id INTEGER, name TEXT, username TEXT,
    text TEXT, mtype TEXT, file_id TEXT, date INTEGER,
    is_owner INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    edited INTEGER DEFAULT 0,
    PRIMARY KEY(conn_id, chat_id, msg_id))""")
db.execute("""CREATE TABLE IF NOT EXISTS edits(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conn_id TEXT, chat_id INTEGER, msg_id INTEGER,
    old_text TEXT, new_text TEXT, edited_at INTEGER)""")
db.execute("CREATE TABLE IF NOT EXISTS conns(conn_id TEXT PRIMARY KEY, owner_id INTEGER)")
db.execute("CREATE TABLE IF NOT EXISTS notices(bot_msg_id INTEGER PRIMARY KEY, conn_id TEXT, chat_id INTEGER, msg_id INTEGER)")
db.commit()


def ensure_column(table, col, decl):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        db.commit()


for c, d in [("is_owner", "INTEGER DEFAULT 0"), ("deleted", "INTEGER DEFAULT 0"),
             ("deleted_at", "INTEGER"), ("edited", "INTEGER DEFAULT 0")]:
    ensure_column("messages", c, d)

conn_owner: dict[str, int] = {}
roulette: dict[int, dict] = {}


async def get_owner(conn_id: str) -> int:
    if conn_id in conn_owner:
        return conn_owner[conn_id]
    row = db.execute("SELECT owner_id FROM conns WHERE conn_id=?", (conn_id,)).fetchone()
    if row:
        conn_owner[conn_id] = row[0]
        return row[0]
    c = await bot.get_business_connection(conn_id)
    conn_owner[conn_id] = c.user.id
    db.execute("INSERT OR REPLACE INTO conns VALUES(?,?)", (conn_id, c.user.id))
    db.commit()
    return c.user.id


# ---------------- УТИЛИТЫ ----------------
def media_of(m: Message):
    if m.photo:
        return "photo", m.photo[-1].file_id
    if m.voice:
        return "voice", m.voice.file_id
    if m.video_note:
        return "video_note", m.video_note.file_id
    if m.video:
        return "video", m.video.file_id
    if m.animation:
        return "animation", m.animation.file_id
    if m.audio:
        return "audio", m.audio.file_id
    if m.document:
        return "document", m.document.file_id
    if m.sticker:
        return "sticker", m.sticker.file_id
    return None, None


SENDERS = {
    "photo": ("send_photo", "photo", "photo.jpg"),
    "voice": ("send_voice", "voice", "voice.ogg"),
    "video_note": ("send_video_note", "video_note", "circle.mp4"),
    "video": ("send_video", "video", "video.mp4"),
    "animation": ("send_animation", "animation", "anim.mp4"),
    "audio": ("send_audio", "audio", "audio.mp3"),
    "document": ("send_document", "document", "file.bin"),
    "sticker": ("send_sticker", "sticker", "sticker.webp"),
}


async def send_media(mtype: str, file_id: str, caption: str = "", download: bool = False):
    method, arg, fname = SENDERS[mtype]
    media = file_id
    if download:
        try:
            buf = BytesIO()
            await bot.download(file_id, destination=buf)
            media = BufferedInputFile(buf.getvalue(), filename=fname)
        except Exception:
            media = file_id
    kwargs = {"chat_id": OWNER_ID, arg: media}
    if mtype not in ("video_note", "sticker"):
        kwargs["caption"] = caption
    await getattr(bot, method)(**kwargs)
    if mtype in ("video_note", "sticker") and caption:
        await bot.send_message(OWNER_ID, caption)


def who(name, username, uid) -> str:
    u = f" @{username}" if username else ""
    return f"<b>{html.escape(name or '—')}</b>{u} (<code>{uid}</code>)"


def estimate_reg_date(uid: int) -> str:
    pts = [
        (1_000_000, "2013-08"), (10_000_000, "2014-01"), (60_000_000, "2014-10"),
        (100_000_000, "2015-06"), (180_000_000, "2016-01"), (300_000_000, "2016-12"),
        (450_000_000, "2017-12"), (700_000_000, "2018-12"), (1_000_000_000, "2019-12"),
        (1_500_000_000, "2020-12"), (2_100_000_000, "2021-12"), (5_000_000_000, "2022-12"),
        (6_500_000_000, "2023-12"), (7_700_000_000, "2024-12"),
    ]

    def ts(s):
        y, mth = map(int, s.split("-"))
        return datetime(y, mth, 1, tzinfo=timezone.utc).timestamp()

    if uid <= pts[0][0]:
        return pts[0][1]
    if uid >= pts[-1][0]:
        return f"{pts[-1][1]} или позже"
    for (i1, d1), (i2, d2) in zip(pts, pts[1:]):
        if i1 <= uid <= i2:
            t = ts(d1) + (ts(d2) - ts(d1)) * (uid - i1) / (i2 - i1)
            return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")
    return "неизвестно"


async def capture_media_on_plus(target: Message):
    mtype, file_id = media_of(target)
    if mtype not in ("photo", "voice", "video_note", "video", "animation", "document"):
        return

    sender = target.from_user
    label = {
        "photo": "🖼 Фото", "voice": "🎤 Голосовое", "video_note": "⭕ Кружок",
        "video": "📹 Видео", "animation": "🎞 GIF", "document": "📄 Файл",
    }[mtype]
    cap = f"{label} (сохранено ответом) от "
    cap += who(sender.full_name if sender else "—", sender.username if sender else None,
               sender.id if sender else 0)
    text = target.text or target.caption or ""
    if text:
        cap += f"\n{html.escape(text)}"

    try:
        await send_media(mtype, file_id, cap, download=True)
    except Exception as e:
        await bot.send_message(OWNER_ID, f"⚠️ Не удалось сохранить медиа: {e}")


# ---------------- БИЗНЕС-ПОДКЛЮЧЕНИЕ ----------------
@dp.business_connection()
async def on_connection(c: BusinessConnection):
    conn_owner[c.id] = c.user.id
    db.execute("INSERT OR REPLACE INTO conns VALUES(?,?)", (c.id, c.user.id))
    db.commit()
    status = "подключён ✅" if c.is_enabled else "отключён ❌"
    await bot.send_message(OWNER_ID, f"Бизнес-аккаунт {who(c.user.full_name, c.user.username, c.user.id)} {status}")


# ---------------- БИЗНЕС-СООБЩЕНИЯ ----------------
@dp.business_message()
async def on_message(m: Message):
    conn_id = m.business_connection_id
    owner = await get_owner(conn_id)
    sender = m.from_user

    if sender and sender.id == owner:
        trigger = (m.text or "").strip()

        replied = m.reply_to_message
        replied_is_media = bool(replied and media_of(replied)[0])
        if (CAPTURE_ON_REPLY and trigger.lower() in SAVE_TRIGGERS
                and replied_is_media and replied.from_user
                and replied.from_user.id != owner):
            await capture_media_on_plus(m.reply_to_message)
            try:
                await bot.delete_business_messages(
                    business_connection_id=conn_id, message_ids=[m.message_id]
                )
            except Exception:
                pass
            return

        if trigger.startswith("."):
            if await handle_command(m, conn_id):
                return

        mtype, file_id = media_of(m)
        text = m.text or m.caption or ""
        db.execute(
            "INSERT OR REPLACE INTO messages(conn_id,chat_id,msg_id,user_id,name,username,"
            "text,mtype,file_id,date,is_owner) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            (conn_id, m.chat.id, m.message_id, owner, sender.full_name, sender.username,
             text, mtype, file_id, int(m.date.timestamp())),
        )
        db.commit()
        return

    mtype, file_id = media_of(m)
    text = m.text or m.caption or ""
    db.execute(
        "INSERT OR REPLACE INTO messages(conn_id,chat_id,msg_id,user_id,name,username,"
        "text,mtype,file_id,date,is_owner) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
        (conn_id, m.chat.id, m.message_id, sender.id if sender else 0,
         sender.full_name if sender else "", sender.username if sender else "",
         text, mtype, file_id, int(m.date.timestamp())),
    )
    db.commit()


@dp.edited_business_message()
async def on_edit(m: Message):
    conn_id = m.business_connection_id
    owner = await get_owner(conn_id)
    if m.from_user and m.from_user.id == owner:
        return
    row = db.execute(
        "SELECT text FROM messages WHERE conn_id=? AND chat_id=? AND msg_id=?",
        (conn_id, m.chat.id, m.message_id),
    ).fetchone()
    old = row[0] if row else "(не сохранено)"
    new = m.text or m.caption or ""

    db.execute(
        "INSERT INTO edits(conn_id,chat_id,msg_id,old_text,new_text,edited_at) VALUES(?,?,?,?,?,?)",
        (conn_id, m.chat.id, m.message_id, old, new, int(time.time())),
    )
    db.execute(
        "UPDATE messages SET text=?, edited=1 WHERE conn_id=? AND chat_id=? AND msg_id=?",
        (new, conn_id, m.chat.id, m.message_id),
    )
    db.commit()

    await bot.send_message(
        OWNER_ID,
        f"✏️ <b>Изменено</b> | {who(m.from_user.full_name, m.from_user.username, m.from_user.id)}\n\n"
        f"<b>Было:</b>\n{html.escape(old)}\n\n<b>Стало:</b>\n{html.escape(new)}",
    )


@dp.deleted_business_messages()
async def on_delete(ev: BusinessMessagesDeleted):
    conn_id = ev.business_connection_id
    for mid in ev.message_ids:
        row = db.execute(
            "SELECT user_id,name,username,text,mtype,file_id FROM messages "
            "WHERE conn_id=? AND chat_id=? AND msg_id=?",
            (conn_id, ev.chat.id, mid),
        ).fetchone()

        db.execute(
            "UPDATE messages SET deleted=1, deleted_at=? WHERE conn_id=? AND chat_id=? AND msg_id=?",
            (int(time.time()), conn_id, ev.chat.id, mid),
        )
        db.commit()

        if not row:
            continue
        uid, name, uname, text, mtype, fid = row
        cap = f"🗑 <b>Удалено</b> | {who(name, uname, uid)}"
        if text:
            cap += f"\n\n{html.escape(text)}"
        if mtype and fid and SEND_DELETED_MEDIA:
            try:
                await send_media(mtype, fid, cap)
                continue
            except Exception:
                pass
        if mtype:
            sent = await bot.send_message(
                OWNER_ID,
                cap + f"\n\n<i>Ответь «{', '.join(SAVE_TRIGGERS)}», чтобы получить файл</i>",
            )
            db.execute("INSERT OR REPLACE INTO notices VALUES(?,?,?,?)",
                       (sent.message_id, conn_id, ev.chat.id, mid))
            db.commit()
        else:
            await bot.send_message(OWNER_ID, cap)


# ---------------- /start, ПОМОЩЬ, MINI APP ----------------
COMMANDS_INFO = {
    "spam": ("⚡ .spam", ".spam N текст\n\nОтправляет текст N раз подряд в чат. "
                        "Максимум 25 сообщений даже если указано больше. "
                        "Команда сразу удаляется после отправки."),
    "flip": ("🪙 .flip", ".flip\n\nПодбрасывает монетку: Орёл или Решка."),
    "ping": ("🏓 .ping", ".ping\n\nПоказывает скорость отклика бота в миллисекундах."),
    "check": ("🔍 .check", ".check\n\nПоказывает информацию о собеседнике: ID, имя, "
                          "юзернейм, bio, дату рождения (если открыта) и примерную "
                          "дату регистрации аккаунта."),
    "cc": ("🔫 .cc", ".cc\n\nРусская рулетка: барабан на 6, один патрон заряжен. "
                   "Каждый вызов — новая попытка."),
    "plus": ("➕ +", "+ (ответом на фото, голосовое или кружок собеседника)\n\n"
                     "Сохраняет именно это медиа и присылает тебе копию в ЛС. "
                     "Сам знак + сразу удаляется. Обычные медиа без + не сохраняются."),
}


@dp.message(CommandStart())
async def cmd_start(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"help:{key}")]
        for key, (label, _desc) in COMMANDS_INFO.items()
    ] + [
        [InlineKeyboardButton(text="📂 Открыть историю переписок",
                             web_app=WebAppInfo(url=MINIAPP_URL))]
    ])
    await m.answer(
        "👋 Привет! Я твой личный бизнес-бот.\n\n"
        "Подключён к твоему бизнес-аккаунту и сохраняю переписку: "
        "изменённые и удалённые сообщения. Медиа сохраняются только если "
        "ответить на него знаком <b>+</b>.\n\n"
        "Жми на кнопку команды, чтобы узнать как её использовать, "
        "или открой историю переписок ниже.",
        reply_markup=kb,
    )


@dp.callback_query(F.data.startswith("help:"))
async def cb_help(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    info = COMMANDS_INFO.get(key)
    await call.answer()
    if info:
        await call.message.answer(f"<b>{info[0]}</b>\n\n{info[1]}")


# ---------------- КОМАНДЫ ----------------
async def reply(m: Message, text: str):
    return await bot.send_message(
        m.chat.id, text, business_connection_id=m.business_connection_id
    )


KNOWN_COMMANDS = {".spam", ".flip", ".ping", ".check", ".cc"}


async def handle_command(m: Message, conn_id: str) -> bool:
    parts = m.text.split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd not in KNOWN_COMMANDS:
        return False

    try:
        await bot.delete_business_messages(
            business_connection_id=conn_id, message_ids=[m.message_id]
        )
    except Exception:
        pass

    if cmd == ".spam":
        if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
            await reply(m, "Использование: <code>.spam N текст</code>")
            return True
        n = max(1, min(int(parts[1]), 25))
        text = parts[2]
        for _ in range(n):
            await bot.send_message(m.chat.id, text, business_connection_id=conn_id,
                                   parse_mode=None)
            await asyncio.sleep(0.4)

    elif cmd == ".flip":
        await reply(m, "🪙 " + random.choice(["Орёл", "Решка"]))

    elif cmd == ".ping":
        t = time.perf_counter()
        msg = await reply(m, "🏓 ...")
        ms = (time.perf_counter() - t) * 1000
        await bot.edit_message_text(f"🏓 Pong! <b>{ms:.0f} мс</b>", chat_id=m.chat.id,
                                    message_id=msg.message_id, business_connection_id=conn_id)

    elif cmd == ".check":
        uid = m.chat.id
        lines = [
            "🔍 <b>Информация о собеседнике</b>",
            f"ID: <code>{uid}</code>",
            f"Имя: {html.escape(m.chat.full_name or '—')}",
            f"Юзернейм: {'@' + m.chat.username if m.chat.username else '—'}",
            f"Дата регистрации (≈): {estimate_reg_date(uid)}",
        ]
        try:
            full = await bot.get_chat(uid)
            if full.bio:
                lines.append(f"Bio: {html.escape(full.bio)}")
            if full.birthdate:
                b = full.birthdate
                lines.append(f"Дата рождения: {b.day:02d}.{b.month:02d}" + (f".{b.year}" if b.year else ""))
            if full.personal_chat:
                lines.append(f"Личный канал: @{full.personal_chat.username}")
        except Exception:
            pass
        lines.append(f"Ссылка: <a href='tg://user?id={uid}'>профиль</a>")
        await reply(m, "\n".join(lines))

    elif cmd == ".cc":
        st = roulette.setdefault(m.chat.id, {"bullet": random.randint(1, 6), "shot": 0})
        st["shot"] += 1
        if st["shot"] == st["bullet"]:
            roulette.pop(m.chat.id)
            await reply(m, f"💥 БАХ! Выстрел на попытке {st['shot']}. Проигрыш.")
        else:
            await reply(m, f"🔫 Щёлк... выжил (попытка {st['shot']}/6)")

    return True


@dp.message(F.chat.type == "private", F.reply_to_message)
async def on_owner_reply_to_notice(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    if (m.text or "").strip().lower() not in SAVE_TRIGGERS:
        return
    row = db.execute(
        "SELECT conn_id, chat_id, msg_id FROM notices WHERE bot_msg_id=?",
        (m.reply_to_message.message_id,),
    ).fetchone()
    if not row:
        return
    conn_id, chat_id, msg_id = row
    mrow = db.execute(
        "SELECT user_id,name,username,mtype,file_id FROM messages "
        "WHERE conn_id=? AND chat_id=? AND msg_id=?",
        (conn_id, chat_id, msg_id),
    ).fetchone()
    if not mrow or not mrow[4]:
        await m.reply("Файл не найден в базе.")
        return
    uid, name, uname, mtype, fid = mrow
    cap = f"🗑 Удалённое медиа | {who(name, uname, uid)}"
    try:
        await send_media(mtype, fid, cap, download=True)
    except Exception:
        await m.reply("Telegram уже не отдаёт этот файл.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
