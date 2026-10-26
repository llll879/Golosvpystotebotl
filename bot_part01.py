# bot_part01.py — часть 1 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
"""
Голос в пустоте — анонимный бот эмоциональной поддержки.
aiogram 3.x + aiohttp (веб-сервер и самопинг для Render).
БД: SQLite (по умолчанию) или PostgreSQL (если задан DATABASE_URL).
"""
import asyncio
import html
import io
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           MessageReactionUpdated, ReactionTypeEmoji, ReplyParameters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ───────────────────────── Настройки ─────────────────────────
TOKEN = os.environ["BOT_TOKEN"]
OWNERS = {int(x) for x in os.getenv("MAIN_ADMIN_IDS", "").replace(" ", "").split(",") if x}
PORT = int(os.getenv("PORT", "10000"))
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "600"))  # 10 минут
SELF_URL = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}

DEFAULT_WELCOME = (
    "👋 Привет! Это <b>«Голос в пустоте»</b> — анонимный бот эмоциональной поддержки.\n\n"
    "Здесь можно выговориться, и тебя выслушают. Никто не узнает, кто ты, "
    "а наши слушатели остаются анонимными тоже.\n\n"
    "Просто напиши сообщение — к тебе подключится один из слушателей 💜"
)
TEXT_KEYS = {
    "sent_ok": ("Сообщение принято", "✅ Сообщение передано. Скоро к тебе подключится слушатель 💜"),
    "closed": ("Диалог завершён", "🔒 Диалог завершён. Если захочешь поговорить снова — просто напиши 💜"),
    "banned": ("Сообщение для забаненных", "🚫 Доступ к боту ограничен."),
    "no_group": ("Бот не настроен", "⚙️ Бот ещё не настроен, загляни чуть позже."),
    "apply_intro": ("Анкета: вступление",
                    "🙋 <b>Стать слушателем</b>\n\nМы ищем добрых и внимательных людей, готовых поддерживать других. "
                    "Заполните небольшую анкету — главные админы её рассмотрят. Это займёт пару минут."),
    "apply_sent": ("Анкета: отправлена", "✅ Анкета отправлена! Мы рассмотрим её и обязательно напишем вам здесь 💜"),
    "apply_accepted": ("Анкета: принята",
                       "🎉 Ваша анкета принята! Скоро с вами свяжется главный админ и расскажет, что делать дальше 💜"),
    "apply_rejected": ("Анкета: отклонена",
                       "💔 К сожалению, сейчас мы не можем принять вашу анкету. Спасибо, что хотели помогать другим — "
                       "вы можете попробовать позже."),
    "spam_ban": ("Автобан за спам", "🚫 Вы заблокированы за спам. Если это ошибка — попробуйте обратиться позже."),
    "admin_rest": ("Слушатель ушёл отдыхать", "💤 Слушатель отошёл отдохнуть. Скоро к вам подключится другой — подождите немного 💜"),
    "rules_users": ("Правила бота (для пользователей)",
                    "📜 <b>Правила «Голоса в пустоте»</b>\n\n"
                    "1. Здесь анонимно: не называй личные данные (адрес, телефон, соцсети) — ни свои, ни чужие.\n"
                    "2. Будь уважителен к слушателям: оскорбления и агрессия — повод для блокировки.\n"
                    "3. Не спамь: слишком много сообщений подряд = автоматический бан.\n"
                    "4. Слушатели — не врачи и не психотерапевты. В кризисной ситуации обратись на горячую линию или в скорую помощь.\n"
                    "5. Запрещены реклама, угрозы и незаконный контент.\n\n"
                    "Завершить диалог можно командой /stop."),
    "rules_admins": ("Правила для админов (приветствие в группе)",
                     "📜 <b>Правила для админов</b>\n\n"
                     "1. Анонимность превыше всего: не раскрывайте юзернейм, ID и личные данные пользователям.\n"
                     "2. Не разглашайте то, что рассказали пользователи, — ни в группе, ни за её пределами.\n"
                     "3. Берите пользователя, только если готовы ответить сейчас. Устали — /rest.\n"
                     "4. Общайтесь бережно: без осуждения, давления и советов «просто не думай об этом».\n"
                     "5. Не обещайте невозможного; при угрозе жизни напомните про экстренные службы.\n"
                     "6. Заметки коллегам пишите с «/» в начале — они не уйдут пользователю."),
}
GENDER = {"m": "М", "f": "Ж", "o": "Другое"}


# ───────────────────────── База данных ─────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT, username TEXT,
  gender TEXT, tag TEXT, greeting TEXT, level INTEGER DEFAULT 1, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(user_id BIGINT PRIMARY KEY, username TEXT, name TEXT,
  banned INTEGER DEFAULT 0, alive INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS groups(chat_id BIGINT PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS dialogs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT, admin_id INTEGER,
  status TEXT, group_id BIGINT, card_msg_id BIGINT, created TEXT);
CREATE TABLE IF NOT EXISTS gmap(chat_id BIGINT, msg_id BIGINT, dialog_id INTEGER, PRIMARY KEY(chat_id, msg_id));
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, admin_id INTEGER, dialog_id INTEGER, val INTEGER);
CREATE TABLE IF NOT EXISTS rests(admin_id INTEGER PRIMARY KEY, end_at TEXT);
CREATE TABLE IF NOT EXISTS relay(src_chat BIGINT, src_msg BIGINT, dst_chat BIGINT, dst_msg BIGINT, dialog_id INTEGER, extra BIGINT, PRIMARY KEY(src_chat, src_msg));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT, username TEXT, name TEXT, answers TEXT, status TEXT, created TEXT, decided_at TEXT, group_id BIGINT, card_msg_id BIGINT);
CREATE INDEX IF NOT EXISTS relay_dst ON relay(dst_chat, dst_msg);
CREATE TABLE IF NOT EXISTS topics(user_id BIGINT, chat_id BIGINT, thread_id BIGINT, PRIMARY KEY(user_id, chat_id));
CREATE INDEX IF NOT EXISTS topics_thread ON topics(chat_id, thread_id)
"""


class DB:
    is_pg = False

    async def init(self):
        url = os.getenv("DATABASE_URL", "")
        if url.startswith("postgres"):
            import asyncpg
            self.is_pg = True
            self.pg = await asyncpg.create_pool(url, min_size=1, max_size=5)
        else:
            import aiosqlite
            self.lite = await aiosqlite.connect(os.getenv("SQLITE_PATH", "bot.db"))
            self.lite.row_factory = aiosqlite.Row
        for st in SCHEMA.split(";"):
            if st.strip():
                if self.is_pg:
                    st = st.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                await self.execute(st)

    def _q(self, sql):
        if not self.is_pg:
            return sql
        n = 0

        def rep(_):
            nonlocal n
            n += 1
            return f"${n}"
        return re.sub(r"\?", rep, sql)

    async def execute(self, sql, *a):
        sql = self._q(sql)
        if self.is_pg:
            await self.pg.execute(sql, *a)
        else:
            await self.lite.execute(sql, a)
            await self.lite.commit()

    async def fetchall(self, sql, *a):
        sql = self._q(sql)
        if self.is_pg:
            return [dict(r) for r in await self.pg.fetch(sql, *a)]
        async with self.lite.execute(sql, a) as c:
            rows = await c.fetchall()
        await self.lite.commit()
        return [dict(r) for r in rows]

    async def fetchone(self, sql, *a):
        r = await self.fetchall(sql, *a)
        return r[0] if r else None


db = DB()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def esc(s):
    return html.escape(str(s or ""))


async def get_setting(key, default=None):
    r = await db.fetchone("SELECT value FROM settings WHERE key=?", key)
    return r["value"] if r else default


async def set_setting(key, value):
    await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     key, value)


async def del_setting(key):
    await db.execute("DELETE FROM settings WHERE key=?", key)


async def txt(key):
    return await get_setting("t_" + key, TEXT_KEYS[key][1])


async def log(actor, action, details=""):
    logging.info("%s | %s | %s", actor, action, details)
    await db.execute("INSERT INTO logs(ts,actor,action,details) VALUES(?,?,?,?)", now(), str(actor), action, details)


async def ensure_owners():
    for oid in OWNERS:
        if not await db.fetchone("SELECT id FROM admins WHERE user_id=?", oid):
            await db.execute(
                "INSERT INTO admins(user_id,username,gender,tag,greeting,level,active) VALUES(?,?,?,?,?,2,1)",
                oid, None, "—", "#главный", "Привет! Я рядом, расскажи, что у тебя на душе 💜")


# ───────────────────────── Хелперы ─────────────────────────
async def get_admin(u):
    r = await db.fetchone("SELECT * FROM admins WHERE user_id=? AND active=1", u.id)
    if r:
        un = u.username.lower() if u.username else None
        if un and un != r["username"]:
            await db.execute("UPDATE admins SET username=? WHERE id=?", un, r["id"])
            r["username"] = un
        return r
    if u.username:
        r = await db.fetchone("SELECT * FROM admins WHERE user_id IS NULL AND lower(username)=?", u.username.lower())
        if r:
            await db.execute("UPDATE admins SET user_id=? WHERE id=?", u.id, r["id"])
            r["user_id"] = u.id
            return r if r["active"] else None
    return None


async def is_main(u):
    if u.id in OWNERS:
        return True
    a = await get_admin(u)
    return bool(a and a["level"] == 2)


async def flag(key, default="1"):
    return (await get_setting(key, default)) == "1"


async def ev(kind, admin_id, dialog_id, val=0):
    await db.execute("INSERT INTO events(ts,kind,admin_id,dialog_id,val) VALUES(?,?,?,?,?)",
                     now(), kind, admin_id, dialog_id, val)


def admin_ref(adm):
    if adm.get("username"):
        return "@" + esc(adm["username"])
    if adm.get("user_id"):
        return f'<a href="tg://user?id={adm["user_id"]}">{esc(adm["tag"])}</a>'
    return esc(adm["tag"])


async def admin_label(adm, full=False):
    """Метка админа в группе: юзернейм (по умолчанию) или тег — переключается в настройках."""
    if await flag("f_label"):
        ref = admin_ref(adm)
        return f"{ref} · {esc(adm['tag'])}" if full else ref
    return esc(adm["tag"])


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def mins_since(s):
    try:
        return int((datetime.now(timezone.utc) - _parse(s)).total_seconds() // 60)
    except Exception:  # noqa
        return 0


async def wait_seconds(did):
    d = await db.fetchone("SELECT created FROM dialogs WHERE id=?", did)
    try:
        return int((datetime.now(timezone.utc) - _parse(d["created"])).total_seconds())
    except Exception:  # noqa
        return -1


def dlink(d):
    g = str(d["group_id"])
    if g.startswith("-100") and d.get("card_msg_id"):
        return f'<a href="https://t.me/c/{g[4:]}/{d["card_msg_id"]}">#{d["id"]}</a>'
    return f"#{d['id']}"


# ───────── Антиспам ─────────
SPAM_N = int(os.getenv("SPAM_LIMIT", "8"))      # сообщений…
SPAM_SEC = int(os.getenv("SPAM_WINDOW", "10"))  # …за столько секунд = автобан
_hits: dict = {}
_last_mg: dict = {}


def is_spam(uid, media_group_id=None):
    if media_group_id:  # альбом считаем за одно сообщение
        if _last_mg.get(uid) == media_group_id:
            return False
        _last_mg[uid] = media_group_id
    t = time.monotonic()
    dq = _hits.setdefault(uid, deque())
    dq.append(t)
    while dq and t - dq[0] > SPAM_SEC:
        dq.popleft()
    return len(dq) > SPAM_N


def start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Посмотреть админов", callback_data="adm_list")],
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
        [InlineKeyboardButton(text="🙋 Стать админом", callback_data="apply")]])


async def pin(chat_id, msg_id):
    try:
        await bot.pin_chat_message(chat_id, msg_id, disable_notification=True)
    except Exception as e:  # noqa
        await log("system", "pin_failed", f"{chat_id}/{msg_id}: {e}")


async def unpin(chat_id, msg_id):
    try:
        await bot.unpin_chat_message(chat_id, msg_id)
    except Exception as e:  # noqa
        await log("system", "unpin_failed", f"{chat_id}/{msg_id}: {e}")


async def rights_note(chat_id):
    try:
        me = await bot.get_chat_member(chat_id, (await bot.me()).id)
    except Exception as e:  # noqa
        return f"⚠️ Не удалось проверить права бота: {esc(e)}"
    if me.status == "creator":
        return ""
    if me.status != "administrator":
        return "⚠️ Бот не админ группы. Сделайте его админом с правами «Закреплять сообщения» и «Удалять сообщения»."
    miss = []
    if not getattr(me, "can_pin_messages", False):
        miss.append("закреплять сообщения")
    if not getattr(me, "can_delete_messages", False):
        miss.append("удалять сообщения")
    try:
        if getattr(await bot.get_chat(chat_id), "is_forum", False) and not getattr(me, "can_manage_topics", False):
            miss.append("управлять темами")
    except Exception:  # noqa
        pass
    return ("⚠️ У бота нет права: " + ", ".join(miss) + ".") if miss else ""
