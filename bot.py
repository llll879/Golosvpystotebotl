"""
Голос в пустоте — анонимный бот эмоциональной поддержки.
aiogram 3.x + aiohttp (веб-сервер и самопинг для Render).
БД: SQLite (по умолчанию) или PostgreSQL (если задан DATABASE_URL).
"""
import asyncio
import html
import io
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
                           ReactionTypeEmoji, ReplyParameters)

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
CREATE TABLE IF NOT EXISTS relay(src_chat BIGINT, src_msg BIGINT, dst_chat BIGINT, dst_msg BIGINT, dialog_id INTEGER, extra BIGINT, PRIMARY KEY(src_chat, src_msg))
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
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")]])


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
    return ("⚠️ У бота нет права: " + ", ".join(miss) + ".") if miss else ""


# ───────── Пересылка без «переслано» + запасные способы ─────────
async def relay_copy(m: Message, chat_id):
    """Копирует сообщение. Если Telegram отказал (премиум-стикеры, кастомные эмодзи и т.п.) — пробует запасные способы."""
    try:
        return await m.copy_to(chat_id)
    except TelegramForbiddenError:
        raise
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await m.copy_to(chat_id)
    except TelegramAPIError as e:
        await log("system", "copy_failed", f"{type(e).__name__}: {e}")
        if m.sticker:
            return await bot.send_sticker(chat_id, m.sticker.file_id)
        if m.text:
            ents = [x for x in (m.entities or []) if x.type != "custom_emoji"]
            return await bot.send_message(chat_id, m.text, entities=ents or None, parse_mode=None)
        raise


async def relay_add(src_chat, src_msg, dst_chat, dst_msg, dialog_id, extra=None):
    await db.execute(
        "INSERT INTO relay(src_chat,src_msg,dst_chat,dst_msg,dialog_id,extra) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(src_chat,src_msg) DO UPDATE SET dst_chat=excluded.dst_chat, dst_msg=excluded.dst_msg, "
        "dialog_id=excluded.dialog_id, extra=excluded.extra",
        src_chat, src_msg, dst_chat, dst_msg, dialog_id, extra)


# ───────── Отдых админов ─────────
async def rest_until(admin_id):
    r = await db.fetchone("SELECT end_at FROM rests WHERE admin_id=?", admin_id)
    return r["end_at"] if r and r["end_at"] > now() else None


async def start_rest(adm, mins):
    until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("INSERT INTO rests(admin_id,end_at) VALUES(?,?) "
                     "ON CONFLICT(admin_id) DO UPDATE SET end_at=excluded.end_at", adm["id"], until)
    rows = await db.fetchall("SELECT id, user_id FROM dialogs WHERE admin_id=? AND status='active'", adm["id"])
    for d in rows:
        await safe(release_dialog(d["id"]))
        await safe(bot.send_message(d["user_id"], await txt("admin_rest")))
    await ev("rest", adm["id"], 0, mins)
    return until, len(rows)


async def end_rest(admin_id):
    await db.execute("DELETE FROM rests WHERE admin_id=?", admin_id)


async def end_expired_rests():
    rows = await db.fetchall("SELECT x.admin_id, a.user_id FROM rests x JOIN admins a ON a.id=x.admin_id "
                             "WHERE x.end_at<=?", now())
    for x in rows:
        await end_rest(x["admin_id"])
        if x["user_id"]:
            await safe(bot.send_message(x["user_id"], "✅ Отдых закончился — вы снова в строю! Очередь: /queue"))


def parse_minutes(arg, default=30):
    if not arg:
        return default
    mm = re.match(r"(\d+)\s*([a-zA-Zа-яА-Я]*)", arg.strip())
    if not mm:
        return default
    n, u = int(mm.group(1)), mm.group(2).lower()
    return n * 60 if u.startswith(("h", "ч")) else n


async def touch_user(u):
    await db.execute(
        "INSERT INTO users(user_id,username,name,first_seen,last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,name=excluded.name,"
        "last_seen=excluded.last_seen,alive=1",
        u.id, u.username, u.full_name, now(), now())
    return await db.fetchone("SELECT * FROM users WHERE user_id=?", u.id)


async def main_group():
    v = await get_setting("main_group")
    return int(v) if v else None


async def send_html(chat_id, text, photo=None, markup=None):
    if photo:
        if len(text) <= 1000:
            return await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
        await bot.send_photo(chat_id, photo)
    return await bot.send_message(chat_id, text, reply_markup=markup)


async def safe(coro):
    try:
        return await coro
    except Exception as e:  # noqa
        logging.warning("safe: %s", e)


async def gmap_add(chat_id, msg_id, dialog_id):
    await db.execute("INSERT INTO gmap(chat_id,msg_id,dialog_id) VALUES(?,?,?) "
                     "ON CONFLICT(chat_id,msg_id) DO UPDATE SET dialog_id=excluded.dialog_id", chat_id, msg_id, dialog_id)


def btn(t, d):
    return InlineKeyboardButton(text=t, callback_data=d)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def post_card(did, group_id, text, reply_to=None):
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    card = await bot.send_message(group_id, text, reply_markup=kb([[btn("🙋 Взять пользователя", f"take:{did}")]]),
                                  reply_parameters=rp)
    await pin(group_id, card.message_id)
    await db.execute("UPDATE dialogs SET card_msg_id=? WHERE id=?", card.message_id, did)
    await gmap_add(group_id, card.message_id, did)


async def release_dialog(did):
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", did)
    if not d or d["status"] == "closed":
        return
    await db.execute("UPDATE dialogs SET admin_id=NULL, status='pending' WHERE id=?", did)
    if d["card_msg_id"]:
        await unpin(d["group_id"], d["card_msg_id"])
    await post_card(did, d["group_id"], f"🔄 Обращение #{did} снова ждёт слушателя")


async def close_dialog(did):
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", did)
    if not d or d["status"] == "closed":
        return
    await db.execute("UPDATE dialogs SET status='closed' WHERE id=?", did)
    if d["card_msg_id"]:
        await unpin(d["group_id"], d["card_msg_id"])
        await safe(bot.edit_message_text(f"🔒 Обращение #{did} закрыто", chat_id=d["group_id"],
                                         message_id=d["card_msg_id"]))


async def release_admin_dialogs(admin_id):
    rows = await db.fetchall("SELECT id FROM dialogs WHERE admin_id=? AND status='active'", admin_id)
    for r in rows:
        await safe(release_dialog(r["id"]))


async def dialog_from_reply(m: Message):
    if not m.reply_to_message:
        return None
    mp = await db.fetchone("SELECT dialog_id FROM gmap WHERE chat_id=? AND msg_id=?",
                           m.chat.id, m.reply_to_message.message_id)
    if not mp:
        return None
    return await db.fetchone("SELECT * FROM dialogs WHERE id=?", mp["dialog_id"])


# ───────────────────────── Роутеры ─────────────────────────
panel = Router()
r = Router()
NOCMD = ~F.text.startswith("/")
PRIV = F.chat.type == ChatType.PRIVATE
GRP = F.chat.type.in_(GROUPS)


class IsMain(Filter):
    async def __call__(self, event) -> bool:
        return await is_main(event.from_user)


class IsAdm(Filter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user and await get_admin(event.from_user))


panel.message.filter(IsMain())
panel.callback_query.filter(IsMain())


class S(StatesGroup):
    w_text = State()
    w_photo = State()
    text_edit = State()
    bc = State()
    edit_adm = State()


class AddAdm(StatesGroup):
    ident = State()
    gender = State()
    tag = State()
    greet = State()


# ═════════════════════ ПОЛЬЗОВАТЕЛЬ ═════════════════════
@r.message(CommandStart(), PRIV)
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    u = await touch_user(m.from_user)
    if u["banned"]:
        return await m.answer(await txt("banned"))
    await log(m.from_user.id, "start")
    text = await get_setting("welcome_text", DEFAULT_WELCOME)
    photo = await get_setting("welcome_photo")
    await send_html(m.chat.id, text, photo, start_kb())


@r.callback_query(F.data == "adm_list")
async def adm_list(c: CallbackQuery):
    rows = await db.fetchall("SELECT id, tag, gender FROM admins WHERE active=1 ORDER BY id")
    if not rows:
        return await c.answer("Пока нет слушателей", show_alert=True)
    resting = {x["admin_id"] for x in await db.fetchall("SELECT admin_id FROM rests WHERE end_at>?", now())}
    lines = [f"{esc(a['tag'])} · {esc(a['gender'])}" + (" · 💤 отдыхает" if a["id"] in resting else "")
             for a in rows]
    await c.message.answer("👥 <b>Наши слушатели:</b>\n\n" + "\n".join(lines))
    await c.answer()


def _is_norma(t):
    return bool(t) and t.strip().lower().strip("/!.") in {"норма", "norma"}


@r.message(IsAdm(), F.text.func(_is_norma))
@r.message(Command("norma"))
async def norma(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    aid = adm["id"]

    async def by(kind, since):
        return await cnt("SELECT COUNT(*) c FROM events WHERE kind=? AND admin_id=? AND ts>=?", kind, aid, since)

    periods = (ago(0), ago(7), ago(30), "")
    takes = [await by("take", p) for p in periods]
    reps = [await by("reply", p) for p in periods]
    ins = [await by("in", p) for p in periods]
    closed = await by("close", "")
    live = await cnt("SELECT COUNT(*) c FROM dialogs WHERE admin_id=? AND status='active'", aid)
    row = await db.fetchone("SELECT AVG(val) c FROM events WHERE kind='take' AND admin_id=? AND val>=0", aid)
    avg = f"{float(row['c']) / 60:.1f} мин" if row and row["c"] is not None else "—"
    rank_rows = await db.fetchall("SELECT admin_id, COUNT(*) c FROM events WHERE kind='reply' AND ts>=? "
                                  "GROUP BY admin_id", ago(7))
    mine = reps[1]
    place = 1 + sum(1 for x in rank_rows if x["c"] > mine)
    total = await cnt("SELECT COUNT(*) c FROM admins WHERE active=1")
    rest_n = await by("rest", "")
    rrow = await db.fetchone("SELECT SUM(val) c FROM events WHERE kind='rest' AND admin_id=?", aid)
    rest_min = int(rrow["c"] or 0) if rrow else 0

    def line(label, v):
        return f"{label}: <b>{v[0]}</b> / {v[1]} / {v[2]} / {v[3]}"

    await m.reply(
        f"📋 <b>Норма · {esc(adm['tag'])}</b>\n<i>сегодня / 7 дн / 30 дн / всего</i>\n\n"
        f"{line('Взято пользователей', takes)}\n"
        f"{line('Ваших ответов', reps)}\n"
        f"{line('Сообщений от ваших пользователей', ins)}\n\n"
        f"Сейчас веду: <b>{live}</b> · закрыто вами: {closed}\n"
        f"Среднее время до взятия: {avg}\n"
        f"Отдыхов: {rest_n} (всего {rest_min} мин)\n"
        f"Место по ответам за 7 дней: <b>{place}</b> из {total}\n"
        "<i>Данные копятся с момента обновления бота.</i>")


@r.message(Command("queue"))
async def cmd_queue(m: Message):
    if not m.from_user or not await get_admin(m.from_user):
        return
    rows = await db.fetchall("SELECT * FROM dialogs WHERE status='pending' ORDER BY id LIMIT 20")
    if not rows:
        return await m.reply("✅ Очередь пуста — все пользователи взяты.")
    lines = [f"• {dlink(d)} — ждёт {mins_since(d['created'])} мин" for d in rows]
    await m.reply("⏳ <b>Ждут слушателя:</b>\n" + "\n".join(lines), disable_web_page_preview=True)


@r.message(Command("my"))
async def cmd_my(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    rows = await db.fetchall("SELECT * FROM dialogs WHERE admin_id=? AND status='active' ORDER BY id", adm["id"])
    if not rows:
        return await m.reply("У вас нет активных диалогов.")
    lines = [f"• {dlink(d)} — с {d['created'][5:16]} UTC" for d in rows]
    await m.reply("💬 <b>Ваши активные диалоги:</b>\n" + "\n".join(lines), disable_web_page_preview=True)


@r.message(Command("info"), F.reply_to_message)
async def cmd_info(m: Message):
    if not m.from_user or not await get_admin(m.from_user):
        return
    d = await dialog_from_reply(m)
    if not d:
        return
    who = "—"
    if d["admin_id"]:
        a = await db.fetchone("SELECT * FROM admins WHERE id=?", d["admin_id"])
        if a:
            who = await admin_label(a, full=True)
    await m.reply(f"ℹ️ Обращение #{d['id']}\nСтатус: {d['status']}\nОткрыто: {d['created'][5:16]} UTC\nВедёт: {who}")


@r.message(Command("greeting"))
async def cmd_greeting(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    new = re.sub(r"^/greeting(@\w+)?\s*", "", m.html_text or "", count=1).strip()
    if not new:
        return await m.reply(f"Ваше приветствие сейчас:\n\n{adm['greeting']}\n\n"
                             "Изменить: <code>/greeting новый текст</code>")
    await db.execute("UPDATE admins SET greeting=? WHERE id=?", new, adm["id"])
    await log(m.from_user.id, "own_greeting_changed")
    await m.reply("✅ Приветствие обновлено.")


@r.message(Command("help"))
async def cmd_help(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    text = ("📖 <b>Команды админа</b>\n\n"
            "• Ответ пользователю — реплаем на его сообщение (текст с «/» в начале НЕ отправляется — "
            "можно писать заметки коллегам)\n"
            "• <b>норма</b> — ваша личная статистика\n"
            "• /queue — кто ждёт слушателя\n• /my — ваши активные диалоги\n"
            "• /info — инфо о диалоге (реплаем)\n• /close — закрыть диалог (реплаем)\n"
            "• /release — вернуть пользователя в очередь (реплаем)\n• /ban — забанить (реплаем)\n"
            "• /greeting текст — сменить своё приветствие\n"
            "• /rest [время] — отдых, /back — вернуться, /rests — кто отдыхает\n"
            "• /del (реплаем) — удалить своё сообщение у пользователя\n"
            "• /rules — правила для админов\n"
            "• /guide или «методичка» — полная методичка по боту")
    if await is_main(m.from_user):
        text += "\n\n⭐ <b>Главный админ:</b> /admin — панель (работает и в группе), /bind — привязать группу, /cancel — отмена ввода"
    await m.reply(text)


def _kw(words):
    return lambda t: bool(t) and t.strip().lower().strip("/!.") in words


# ── Отдых ──
@r.message(Command("rest"))
async def cmd_rest(m: Message, command: CommandObject):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    mx = int(os.getenv("REST_MAX", "720"))
    mins = max(5, min(parse_minutes(command.args), mx))
    until, n = await start_rest(adm, mins)
    await log(m.from_user.id, "rest_start", f"{mins} min")
    extra = f"\nДиалогов возвращено в очередь: {n}." if n else ""
    await m.reply(f"💤 Отдых на {mins} мин (до {until[11:16]} UTC). Хороший перерыв!{extra}\n"
                  "Вернуться раньше — /back")


@r.message(Command("back"))
async def cmd_back(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    if not await rest_until(adm["id"]):
        return await m.reply("Вы и так не на отдыхе 🙂")
    await end_rest(adm["id"])
    await log(m.from_user.id, "rest_end")
    await m.reply("✅ С возвращением! Очередь: /queue")


@r.message(Command("rests"))
async def cmd_rests(m: Message):
    if not m.from_user or not await get_admin(m.from_user):
        return
    rows = await db.fetchall("SELECT x.end_at, a.tag FROM rests x JOIN admins a ON a.id=x.admin_id "
                             "WHERE x.end_at>? ORDER BY x.end_at", now())
    if not rows:
        return await m.reply("Сейчас никто не отдыхает.")
    lines = [f"💤 {esc(x['tag'])} — до {x['end_at'][11:16]} UTC" for x in rows]
    await m.reply("<b>Сейчас отдыхают:</b>\n" + "\n".join(lines))


# ── Разбан, правила ──
@r.message(Command("unban"))
async def cmd_unban(m: Message, command: CommandObject):
    if not m.from_user or not await is_main(m.from_user):
        return
    uid = None
    if command.args and command.args.strip().isdigit():
        uid = int(command.args.strip())
    elif m.reply_to_message:
        d = await dialog_from_reply(m)
        uid = d["user_id"] if d else None
    if not uid:
        return await m.reply("Ответьте командой на сообщение пользователя или напишите <code>/unban ID</code>.")
    await db.execute("UPDATE users SET banned=0 WHERE user_id=?", uid)
    await log(m.from_user.id, "unban", str(uid))
    await m.reply("♻️ Пользователь разбанен.")


@r.callback_query(F.data == "rules")
async def rules_cb(c: CallbackQuery):
    await c.message.answer(await txt("rules_users"))
    await c.answer()


@r.message(Command("rules"), GRP)
async def cmd_rules(m: Message):
    if m.from_user and await get_admin(m.from_user):
        await m.reply(await txt("rules_admins"))


@r.message(GRP, F.new_chat_members)
async def g_join(m: Message):
    if not await db.fetchone("SELECT chat_id FROM groups WHERE chat_id=?", m.chat.id):
        return
    rules = await txt("rules_admins")
    for u in m.new_chat_members:
        if u.is_bot:
            continue
        mention = f'<a href="tg://user?id={u.id}">{esc(u.full_name)}</a>'
        await safe(bot.send_message(m.chat.id, f"👋 Добро пожаловать, {mention}!\n\n{rules}\n\n"
                                               "Методичка по боту: /guide"))


# ── Правки сообщений ──
@r.edited_message(PRIV)
async def user_edit(m: Message):
    row = await db.fetchone("SELECT * FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, m.message_id)
    if not row:
        return
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", row["dialog_id"])
    u = await db.fetchone("SELECT banned FROM users WHERE user_id=?", m.chat.id)
    if not d or d["status"] == "closed" or (u and u["banned"]):
        return
    who = ""
    if d["status"] == "active" and d["admin_id"]:
        a = await db.fetchone("SELECT * FROM admins WHERE id=?", d["admin_id"])
        if a:
            who = f" · {await admin_label(a)}"
    rp = ReplyParameters(message_id=row["dst_msg"], allow_sending_without_reply=True)
    try:
        note = await bot.send_message(row["dst_chat"], f"✏️ Пользователь изменил сообщение выше. Новая версия:{who}",
                                      reply_parameters=rp)
        cp = await bot.copy_message(row["dst_chat"], m.chat.id, m.message_id, reply_parameters=rp)
    except Exception as e:  # noqa
        return await log("system", "user_edit_failed", str(e))
    await gmap_add(row["dst_chat"], note.message_id, d["id"])
    await gmap_add(row["dst_chat"], cp.message_id, d["id"])
    await log(m.chat.id, "user_edit", f"dialog {d['id']}")


@r.edited_message(GRP)
async def admin_edit(m: Message):
    row = await db.fetchone("SELECT * FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, m.message_id)
    if not row:
        return
    try:
        if m.text:
            await bot.edit_message_text(m.html_text, chat_id=row["dst_chat"], message_id=row["dst_msg"])
        elif m.caption is not None or m.photo or m.video or m.document or m.animation or m.audio or m.voice:
            await bot.edit_message_caption(chat_id=row["dst_chat"], message_id=row["dst_msg"],
                                           caption=m.html_text or "")
        else:
            return
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await log("system", "admin_edit_failed", str(e))
        return await m.reply("⚠️ Не удалось изменить сообщение у пользователя (Telegram не даёт править "
                             "этот тип сообщений или оно слишком старое).")
    except Exception as e:  # noqa
        return await log("system", "admin_edit_failed", str(e))
    await log(m.from_user.id if m.from_user else 0, "admin_edit", f"dialog {row['dialog_id']}")


# ── Удаление сообщений ──
@r.message(Command("del", "delete"), GRP, F.reply_to_message)
async def g_del(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return
    tgt = m.reply_to_message
    row = await db.fetchone("SELECT * FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, tgt.message_id)
    if not row:
        return await m.reply("Ответьте командой /del на <b>своё</b> сообщение, которое ушло пользователю.")
    if (not tgt.from_user or tgt.from_user.id != m.from_user.id) and not await is_main(m.from_user):
        return await m.reply("Удалять можно только свои сообщения.")
    try:
        await bot.delete_message(row["dst_chat"], row["dst_msg"])
    except Exception as e:  # noqa
        return await m.reply(f"⚠️ Не удалось удалить у пользователя ({esc(e)}). Telegram разрешает удалять "
                             "сообщения бота только в течение 48 часов.")
    await db.execute("DELETE FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, tgt.message_id)
    await safe(bot.delete_message(m.chat.id, tgt.message_id))
    await safe(m.delete())
    await log(m.from_user.id, "admin_delete", f"dialog {row['dialog_id']}")
    await safe(bot.send_message(m.chat.id, "🗑 Сообщение удалено у пользователя."))


@r.message(Command("del", "delete"), PRIV, F.reply_to_message)
async def user_del(m: Message):
    if not await flag("f_userdel"):
        return await m.answer("Удаление сообщений сейчас отключено.")
    row = await db.fetchone("SELECT * FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, m.reply_to_message.message_id)
    if not row:
        return await m.answer("Ответьте командой /del на своё сообщение, которое хотите удалить у слушателей.")
    await safe(bot.delete_message(row["dst_chat"], row["dst_msg"]))
    if row["extra"]:
        await safe(bot.delete_message(row["dst_chat"], row["extra"]))
    await db.execute("DELETE FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, m.reply_to_message.message_id)
    await safe(bot.send_message(row["dst_chat"], f"🗑 Пользователь удалил своё сообщение (обращение #{row['dialog_id']})."))
    await log(m.chat.id, "user_delete", f"dialog {row['dialog_id']}")
    await m.answer("🗑 Сообщение удалено у слушателей. Из этого чата удалите его сами.")


# ───────── Методичка ─────────
GUIDE_ROOT = ("📖 <b>Методичка по боту «Голос в пустоте»</b>\n\n"
              "Здесь описано всё: как устроен бот, каждая команда и каждая кнопка.\nВыберите раздел:")

PAGES = {
    "u_flow": ("🔄 Как устроен бот", (
        "<b>🔄 Как устроен бот</b>\n\n"
        "1️⃣ Пользователь пишет боту /start и видит приветствие с кнопками «👥 Посмотреть админов» и «📜 Правила».\n\n"
        "2️⃣ Любое его сообщение (текст, фото, голосовое, стикер, кружок…) приходит в группу <b>пересланным</b>. "
        "Под ним — карточка «🆕 Новое обращение #N» с кнопкой «🙋 Взять пользователя». Карточка закреплена.\n\n"
        "3️⃣ Первый админ, нажавший кнопку, забирает пользователя: карточка открепляется и меняется на «✅ взял …», "
        "а пользователь получает приветствие этого админа (его можно поменять: /greeting).\n\n"
        "4️⃣ Дальше под каждым новым сообщением пользователя бот пишет «👤 Ведёт: …» — видно, чей это диалог. "
        "Вы отвечаете <b>реплаем</b> на любое сообщение диалога — ответ уходит пользователю без плашки «переслано» "
        "и без вашего имени.\n\n"
        "5️⃣ Диалог заканчивается командой /close либо пользователь пишет /stop. После этого его новое сообщение "
        "создаст новое обращение.\n\n"
        "🔐 Пользователь никогда не видит ваш юзернейм и ID — только тег в списке админов и ваше приветствие.")),
    "u_dialog": ("💬 Диалог: ответы, правки, удаление", (
        "<b>💬 Ведение диалога</b>\n\n"
        "<b>Кнопка «🙋 Взять пользователя»</b> — забирает обращение. Если его уже взяли, бот скажет об этом. "
        "Если вы на отдыхе (/rest) — кнопка не сработает.\n\n"
        "<b>Ответ пользователю</b> — реплаем на пересланное сообщение, на «Ведёт: …» или на карточку. "
        "Реакция 👍 под вашим сообщением = доставлено. Поддерживается всё: текст с форматированием, премиум-эмодзи, "
        "фото, видео, голосовые, кружки, стикеры (в т.ч. премиум), GIF, файлы, аудио, контакты, геолокации.\n\n"
        "<b>Заметки коллегам</b> — сообщение, которое начинается с «/», пользователю <b>не отправляется</b>.\n\n"
        "<b>✏️ Правка.</b> Отредактировали своё сообщение в группе — оно изменится и у пользователя (текст и подпись "
        "к медиа). Если правит пользователь, бот пришлёт в группу «✏️ Пользователь изменил сообщение… Новая версия» "
        "(Telegram не позволяет менять пересланные сообщения).\n\n"
        "<b>🗑 Удаление.</b> Telegram не сообщает ботам об удалении, поэтому — командой: /del реплаем на своё "
        "сообщение → оно удалится у пользователя и в группе (бот может удалять свои сообщения до 48 часов). "
        "Пользователь тоже может написать /del реплаем на своё сообщение — в группе появится пометка «🗑».\n\n"
        "Если бот пишет «Пользователь заблокировал бота» — закройте диалог (/close).")),
    "u_cmds": ("⌨️ Все команды админа", (
        "<b>⌨️ Команды обычного админа</b>\n<i>«реплаем» — ответом на сообщение диалога в группе</i>\n\n"
        "• <b>/help</b> — краткая справка.\n"
        "• <b>/guide</b> или слово <b>методичка</b> — эта методичка.\n"
        "• <b>норма</b> или /norma — ваша статистика.\n"
        "• <b>/queue</b> — кто ждёт слушателя (со ссылками на карточки).\n"
        "• <b>/my</b> — ваши активные диалоги.\n"
        "• <b>/info</b> (реплаем) — статус диалога и кто его ведёт.\n"
        "• <b>/close</b> (реплаем) — закрыть диалог, пользователь получит прощальный текст.\n"
        "• <b>/release</b> (реплаем) — вернуть пользователя в очередь, создаётся новая карточка.\n"
        "• <b>/ban</b> (реплаем) — забанить пользователя (разбан — только главный админ).\n"
        "• <b>/del</b> (реплаем) — удалить своё сообщение у пользователя.\n"
        "• <b>/greeting текст</b> — сменить своё приветствие. Без текста покажет текущее.\n"
        "• <b>/rest [время]</b> — уйти на отдых, напр. <code>/rest 45</code> или <code>/rest 2ч</code>.\n"
        "• <b>/back</b> — вернуться с отдыха раньше.\n"
        "• <b>/rests</b> — кто сейчас отдыхает.\n"
        "• <b>/rules</b> — правила для админов (в группе).")),
    "u_rest": ("💤 Отдых (рест)", (
        "<b>💤 Система отдыха</b>\n\n"
        "<b>/rest 30</b> — отдых на 30 минут (по умолчанию 30, минимум 5, максимум 12 часов; можно писать «2ч»).\n\n"
        "Что происходит:\n"
        "• все ваши активные диалоги возвращаются в очередь — появляются новые карточки;\n"
        "• эти пользователи получают сообщение, что слушатель отошёл, а скоро придёт другой;\n"
        "• кнопка «Взять пользователя» у вас не работает;\n"
        "• в списке админов у пользователей рядом с вашим тегом — «💤 отдыхает».\n\n"
        "<b>/back</b> — вернуться раньше. Когда время выйдет, бот сам снимет отдых и напишет вам.\n"
        "<b>/rests</b> — список отдыхающих. Главный админ может отправить на отдых любого в панели.\n"
        "Время в боте — UTC.")),
    "u_norma": ("📋 «Норма» — ваша статистика", (
        "<b>📋 Норма</b>\nНапишите <b>норма</b> — получите сводку. Колонки: сегодня / 7 дней / 30 дней / всего.\n\n"
        "• <b>Взято пользователей</b> — сколько раз вы нажали «Взять».\n"
        "• <b>Ваших ответов</b> — сколько сообщений вы отправили пользователям.\n"
        "• <b>Сообщений от ваших пользователей</b> — сколько им написали в ваших диалогах.\n"
        "• <b>Сейчас веду</b> — активные диалоги; <b>закрыто вами</b> — завершённые вами.\n"
        "• <b>Среднее время до взятия</b> — как быстро вы забираете новые обращения.\n"
        "• <b>Отдыхи</b> — сколько раз и на сколько минут вы отдыхали.\n"
        "• <b>Место за 7 дней</b> — ваше место среди админов по числу ответов.\n\n"
        "Статистика копится с момента обновления бота.")),
    "u_safe": ("🔐 Анонимность и правила", (
        "<b>🔐 Анонимность и правила</b>\n\n"
        "• Пользователь не видит ваш юзернейм, ID и имя: ваши ответы приходят от бота.\n"
        "• В группе другие админы видят, кто ведёт диалог (метка «Ведёт»). Группа должна быть закрытой и "
        "только для админов.\n"
        "• Не называйте себя и не просите личные данные пользователя.\n"
        "• Не обсуждайте истории пользователей за пределами группы.\n"
        "• Автобан: пользователь, который шлёт сообщения слишком часто, блокируется автоматически — "
        "в группе появится уведомление. Разбанить может главный админ.\n"
        "• Правила для админов: /rules. Правила для пользователей — кнопка «📜 Правила» в /start.")),

    "m_start": ("🚀 Первый запуск и настройка", (
        "<b>🚀 Первый запуск</b>\n\n"
        "<b>Переменные окружения (Render):</b>\n"
        "• <code>BOT_TOKEN</code> — токен от @BotFather (обязательно).\n"
        "• <code>MAIN_ADMIN_IDS</code> — ID владельцев через запятую (обязательно). Их нельзя удалить из бота.\n"
        "• <code>DATABASE_URL</code> — ссылка Postgres (Neon/Supabase). Без неё данные на free-Render пропадают.\n"
        "• Необязательно: <code>PING_INTERVAL</code> (пинг будилки, сек, по умолч. 600), <code>SPAM_LIMIT</code> "
        "(8) и <code>SPAM_WINDOW</code> (10 с) для автобана, <code>REMIND_MIN</code> (10), <code>REST_MAX</code> "
        "(720 мин), <code>SELF_URL</code>.\n\n"
        "<b>Шаги:</b>\n1. Добавьте бота в закрытую группу админом с правами «Закреплять» и «Удалять сообщения».\n"
        "2. Напишите в группе <b>/bind</b> — бот привяжет группу и проверит свои права.\n"
        "3. В личке откройте /admin и настройте приветствие, правила, админов.\n"
        "4. Добавляйте слушателей: «👮 Админы» → «➕ Админ».")),
    "m_panel": ("🗺 Панель /admin: все кнопки", (
        "<b>🗺 Панель /admin</b> (работает и в личке, и в группе)\n\n"
        "• <b>📝 Приветствие</b> — текст/картинка /start, предпросмотр, правила бота и правила админов.\n"
        "• <b>💬 Тексты</b> — системные сообщения бота (принято, закрыто, бан, спам-бан, отдых, правила).\n"
        "• <b>👮 Админы</b> — список, добавление, карточка админа.\n"
        "• <b>🏢 Группы</b> — привязанные группы, выбор активной.\n"
        "• <b>📣 Рассылка</b> — сообщение всем пользователям.\n"
        "• <b>📊 Аналитика</b> — сводка, «По админам», «По дням».\n"
        "• <b>📜 Логи</b> — последние события и файл со всеми.\n"
        "• <b>🚫 Баны</b> — список забаненных и кнопки разбана.\n"
        "• <b>🔧 Настройки</b> — переключатели (метка, реакции, напоминания, автобан, удаление).\n\n"
        "Любой ввод можно отменить командой <b>/cancel</b>.")),
    "m_texts": ("📝 Приветствие, правила, тексты", (
        "<b>📝 Приветствие, правила и тексты</b>\n\n"
        "<b>Приветствие /start:</b> «✏️ Текст» — новый текст (форматирование и премиум-эмодзи сохраняются); "
        "«🖼 Картинка» — фото к приветствию; «🗑 Убрать картинку»; «👁 Предпросмотр» — как увидит пользователь.\n\n"
        "<b>📜 Правила бота</b> — показываются пользователю по кнопке «📜 Правила» под приветствием.\n"
        "<b>📜 Правила админов</b> — бот отправляет их в группе каждому, кто вошёл (и по /rules).\n\n"
        "<b>💬 Тексты</b> — выберите сообщение и отправьте новый текст: «принято», «диалог завершён», "
        "«бан», «автобан за спам», «слушатель на отдыхе», «бот не настроен», оба набора правил.\n\n"
        "Приветствие каждого админа задаётся в его карточке или самим админом: /greeting.")),
    "m_admins": ("👮 Админы: добавление и карточка", (
        "<b>👮 Админы</b>\n\n"
        "<b>Добавление</b> («➕ Админ» / «⭐ Главный админ»): мини-анкета из 4 шагов — "
        "1) @юзернейм или ID, 2) пол, 3) тег (<code>#фина</code>), 4) приветствие. "
        "Если указали юзернейм, админ активируется, когда сам напишет боту.\n\n"
        "<b>Карточка админа:</b>\n"
        "• ✏️ Тег / ✏️ Пол / ✏️ Привет. — правка данных;\n"
        "• 🔁 Уровень — сделать главным или обычным;\n"
        "• ⏯ Вкл/выкл — отключить без удаления (его диалоги вернутся в очередь);\n"
        "• 💤 Отдых / вернуть — отправить на час отдыха или снять отдых;\n"
        "• 🗑 Удалить — с подтверждением.\n\n"
        "Владельцев из <code>MAIN_ADMIN_IDS</code> менять и удалять нельзя. "
        "Пользователям в списке админов видны только тег, пол и пометка об отдыхе.")),
    "m_groups": ("🏢 Группы", (
        "<b>🏢 Группы</b>\n\n"
        "• Привязка: добавьте бота в группу и напишите <b>/bind</b> (может только главный админ). "
        "Бот проверит, есть ли у него права закреплять и удалять сообщения.\n"
        "• В панели: тап по названию — сделать группу активной ⭐ (туда уходят новые обращения), 🗑 — отвязать.\n"
        "• Уже открытые диалоги остаются в своей группе.\n"
        "• Если закрепление не работает — смотрите «📜 Логи»: там будет запись pin_failed/unpin_failed "
        "с причиной (обычно нет прав у бота).")),
    "m_bc": ("📣 Рассылка", (
        "<b>📣 Рассылка</b>\n\n"
        "1. Нажмите «📣 Рассылка» и отправьте сообщение — любое: текст, фото, видео, стикер, голосовое, "
        "премиум-эмодзи.\n"
        "2. Бот покажет предпросмотр и число получателей.\n"
        "3. «✅ Отправить» запускает рассылку в фоне, в конце придёт отчёт: доставлено / нет.\n\n"
        "Получают все, кто не забанен и не заблокировал бота. Тех, кто заблокировал, бот помечает и больше не трогает.")),
    "m_stats": ("📊 Аналитика, логи, баны", (
        "<b>📊 Аналитика, 📜 логи, 🚫 баны</b>\n\n"
        "<b>Аналитика:</b> пользователи (всего, новые, активные, заблокировавшие, повторные), диалоги по статусам, "
        "сообщения, ответы, среднее число сообщений на диалог, админы, группы, рассылки. "
        "«👮 По админам» — рейтинг, «📈 По дням» — график за 7 дней, «🔄 Обновить».\n\n"
        "<b>Логи:</b> последние 20 событий (кто, что, детали) и «📥 Скачать файл» — до 3000 записей. "
        "Сюда же пишутся ошибки (pin_failed, copy_failed, autoban и др.).\n\n"
        "<b>Баны:</b> список и «♻️ Разбанить». Разбан также: <code>/unban</code> реплаем или <code>/unban ID</code>.")),
    "m_settings": ("🔧 Настройки и автобан", (
        "<b>🔧 Настройки</b> (переключатели в /admin → Настройки)\n\n"
        "• <b>Метка в группе</b> — после сообщения пользователя бот пишет «Ведёт: @юзернейм» или «Ведёт: #тег». "
        "Видно только в группе.\n"
        "• <b>Реакция 👍</b> — отметка, что ответ админа доставлен.\n"
        "• <b>Напоминания</b> — если обращение не взяли за 10 минут, бот напомнит в группе (один раз).\n"
        "• <b>Автобан за спам</b> — если пользователь шлёт больше 8 сообщений за 10 секунд (альбом считается "
        "за одно), он банится, активное обращение закрывается, в группе появляется уведомление. "
        "Пороги — переменные <code>SPAM_LIMIT</code> и <code>SPAM_WINDOW</code>. Админы не банятся.\n"
        "• <b>Удаление пользователем</b> — разрешает пользователю /del на свои сообщения.")),
    "m_cmds": ("⌨️ Команды главного админа", (
        "<b>⌨️ Команды главного админа</b>\n<i>вдобавок ко всем командам обычного админа</i>\n\n"
        "• <b>/admin</b> — панель (в личке или в группе).\n"
        "• <b>/cancel</b> — отменить текущий ввод в панели.\n"
        "• <b>/bind</b> (в группе) — привязать группу и проверить права бота.\n"
        "• <b>/unban</b> (реплаем или с ID) — разбанить.\n"
        "• Главный админ может отвечать, закрывать и удалять сообщения в любом диалоге, даже чужом.\n"
        "• <b>/guide</b> — эта методичка (оба раздела).")),
}
U_ORDER = ["u_flow", "u_dialog", "u_cmds", "u_rest", "u_norma", "u_safe"]
M_ORDER = ["m_start", "m_panel", "m_texts", "m_admins", "m_groups", "m_bc", "m_stats", "m_settings", "m_cmds"]


def guide_root_kb(main):
    rows = [[btn("📘 Для обычных админов", "g:u")]]
    if main:
        rows.append([btn("⭐ Для главных админов", "g:m")])
    return kb(rows)


@r.message(Command("guide", "manual"))
@r.message(IsAdm(), F.text.func(_kw({"методичка", "гайд", "guide", "manual"})))
async def cmd_guide(m: Message):
    if not m.from_user or not await get_admin(m.from_user):
        return
    await m.answer(GUIDE_ROOT, reply_markup=guide_root_kb(await is_main(m.from_user)))


@r.callback_query(F.data.startswith("g:"))
async def guide_cb(c: CallbackQuery):
    if not await get_admin(c.from_user):
        return await c.answer("Только для админов", show_alert=True)
    main = await is_main(c.from_user)
    parts = c.data.split(":")
    if parts[1] == "menu":
        return await show(c, GUIDE_ROOT, guide_root_kb(main))
    if parts[1] in ("u", "m"):
        if parts[1] == "m" and not main:
            return await c.answer("Раздел для главных админов", show_alert=True)
        order = U_ORDER if parts[1] == "u" else M_ORDER
        title = "📘 <b>Для обычных админов</b>" if parts[1] == "u" else "⭐ <b>Для главных админов</b>"
        rows = [[btn(PAGES[k][0], f"g:p:{k}")] for k in order] + [[btn("⬅️ Назад", "g:menu")]]
        return await show(c, f"{title}\nВыберите раздел:", kb(rows))
    if parts[1] == "p" and parts[2] in PAGES:
        if parts[2].startswith("m_") and not main:
            return await c.answer("Раздел для главных админов", show_alert=True)
        back = "g:m" if parts[2].startswith("m_") else "g:u"
        return await show(c, PAGES[parts[2]][1], kb([[btn("⬅️ К разделам", back)]]))
    await c.answer()


@r.message(Command("stop"), PRIV)
async def user_stop(m: Message):
    d = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? AND status IN ('pending','active') "
                          "ORDER BY id DESC LIMIT 1", m.from_user.id)
    if not d:
        return await m.answer("Активного диалога нет 🙂")
    await close_dialog(d["id"])
    await safe(bot.send_message(d["group_id"], f"🔒 Пользователь завершил диалог #{d['id']}"))
    await log(m.from_user.id, "user_stop", f"dialog {d['id']}")
    await m.answer(await txt("closed"))


@r.message(PRIV, NOCMD)
async def user_msg(m: Message, state: FSMContext):
    u = await touch_user(m.from_user)
    if u["banned"]:
        return await m.answer(await txt("banned"))
    if (await flag("f_antispam") and is_spam(m.from_user.id, m.media_group_id)
            and not await get_admin(m.from_user)):
        await db.execute("UPDATE users SET banned=1 WHERE user_id=?", m.from_user.id)
        dd = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? AND status IN ('pending','active') "
                               "ORDER BY id DESC LIMIT 1", m.from_user.id)
        if dd:
            await close_dialog(dd["id"])
            await safe(bot.send_message(dd["group_id"], f"🚫 Автобан за спам (обращение #{dd['id']}). "
                                                        "Разбан: /unban реплаем или панель /admin → Баны."))
        await log("system", "autoban", f"user {m.from_user.id}")
        return await m.answer(await txt("spam_ban"))
    gid = await main_group()
    if not gid:
        return await m.answer(await txt("no_group"))
    d = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? AND status IN ('pending','active') "
                          "ORDER BY id DESC LIMIT 1", m.from_user.id)
    adm = None
    if d and d["status"] == "active":
        adm = await db.fetchone("SELECT * FROM admins WHERE id=? AND active=1", d["admin_id"])
        if not adm:  # админа удалили/отключили
            await release_dialog(d["id"])
            d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", d["id"])
    new = False
    if not d:
        await db.execute("INSERT INTO dialogs(user_id,status,group_id,created) VALUES(?,?,?,?)",
                         m.from_user.id, "pending", gid, now())
        d = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? ORDER BY id DESC LIMIT 1", m.from_user.id)
        new = True
    try:
        fm = await bot.forward_message(d["group_id"], m.chat.id, m.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await log("system", "forward_error", str(e))
        return await m.answer("⚠️ Не удалось доставить сообщение, попробуй позже.")
    await gmap_add(d["group_id"], fm.message_id, d["id"])
    lm = None
    if adm:
        await ev("in", adm["id"], d["id"])
        lab = await admin_label(adm)
        lm = await safe(bot.send_message(d["group_id"], f"👤 Ведёт: {lab}",
                                         reply_parameters=ReplyParameters(message_id=fm.message_id,
                                                                          allow_sending_without_reply=True)))
        if lm:
            await gmap_add(d["group_id"], lm.message_id, d["id"])
    elif new:
        await post_card(d["id"], d["group_id"], f"🆕 Новое обращение #{d['id']}", reply_to=fm.message_id)
        await m.answer(await txt("sent_ok"))
    await relay_add(m.chat.id, m.message_id, d["group_id"], fm.message_id, d["id"], lm.message_id if lm else None)
    await log(m.from_user.id, "user_message", f"dialog {d['id']}")


@r.callback_query(F.data.startswith("take:"))
async def take(c: CallbackQuery):
    adm = await get_admin(c.from_user)
    if not adm:
        return await c.answer("Только для админов", show_alert=True)
    ru = await rest_until(adm["id"])
    if ru:
        return await c.answer(f"💤 Вы на отдыхе до {ru[11:16]} UTC. /back — вернуться раньше", show_alert=True)
    did = int(c.data.split(":")[1])
    row = await db.fetchone("UPDATE dialogs SET admin_id=?, status='active' WHERE id=? AND status='pending' "
                            "RETURNING user_id, card_msg_id, group_id", adm["id"], did)
    if not row:
        return await c.answer("Уже взят или закрыт", show_alert=True)
    first = not await cnt("SELECT COUNT(*) c FROM events WHERE kind='take' AND dialog_id=?", did)
    await ev("take", adm["id"], did, await wait_seconds(did) if first else -1)
    if row["card_msg_id"]:
        await unpin(row["group_id"], row["card_msg_id"])
        lab = await admin_label(adm, full=True)
        await safe(bot.edit_message_text(f"✅ Обращение #{did} · взял {lab}",
                                         chat_id=row["group_id"], message_id=row["card_msg_id"]))
    try:
        await send_html(row["user_id"], adm["greeting"])
    except TelegramForbiddenError:
        await close_dialog(did)
        await db.execute("UPDATE users SET alive=0 WHERE user_id=?", row["user_id"])
        return await c.answer("Пользователь заблокировал бота", show_alert=True)
    await log(c.from_user.id, "take", f"dialog {did}, admin {adm['id']}")
    await c.answer("Вы взяли пользователя ✅\nОтвечайте реплаем на его сообщения", show_alert=True)


# ═════════════════════ ГРУППА ═════════════════════
GRP = F.chat.type.in_(GROUPS)


@r.message(Command("bind"), GRP)
async def bind(m: Message):
    if not m.from_user or not await is_main(m.from_user):
        return
    await db.execute("INSERT INTO groups(chat_id,title) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                     m.chat.id, m.chat.title)
    if not await main_group():
        await set_setting("main_group", str(m.chat.id))
    await log(m.from_user.id, "bind_group", str(m.chat.id))
    note = await rights_note(m.chat.id)
    await m.reply("✅ Группа привязана. Активную группу можно выбрать в /admin → Группы." + (f"\n\n{note}" if note else ""))


async def _cmd_guard(m: Message):
    adm = await get_admin(m.from_user) if m.from_user else None
    if not adm:
        return None, None
    d = await dialog_from_reply(m)
    if not d:
        return adm, None
    if d["admin_id"] != adm["id"] and not await is_main(m.from_user) and d["status"] == "active":
        await m.reply("Этого пользователя ведёт другой админ.")
        return adm, None
    return adm, d


@r.message(Command("close"), GRP, F.reply_to_message)
async def g_close(m: Message):
    adm, d = await _cmd_guard(m)
    if not d:
        return
    await close_dialog(d["id"])
    await safe(bot.send_message(d["user_id"], await txt("closed")))
    await ev("close", adm["id"], d["id"])
    await log(m.from_user.id, "close", f"dialog {d['id']}")
    await m.reply(f"🔒 Диалог #{d['id']} закрыт.")


@r.message(Command("release"), GRP, F.reply_to_message)
async def g_release(m: Message):
    adm, d = await _cmd_guard(m)
    if not d or d["status"] != "active":
        return
    await release_dialog(d["id"])
    await log(m.from_user.id, "release", f"dialog {d['id']}")


@r.message(Command("ban"), GRP, F.reply_to_message)
async def g_ban(m: Message):
    adm, d = await _cmd_guard(m)
    if not d:
        return
    await db.execute("UPDATE users SET banned=1 WHERE user_id=?", d["user_id"])
    await close_dialog(d["id"])
    await log(m.from_user.id, "ban", f"user {d['user_id']}")
    await m.reply("🚫 Пользователь забанен (разбан — в панели /admin).")


@r.message(GRP, F.reply_to_message)
async def g_reply(m: Message):
    if not m.from_user or (m.text or m.caption or "").startswith("/"):
        return  # текст с «/» в начале пользователю НЕ отправляется
    d = await dialog_from_reply(m)
    if not d:
        return
    adm = await get_admin(m.from_user)
    if not adm:
        return
    if d["status"] == "closed":
        return await m.reply("Диалог закрыт.")
    if d["status"] == "pending":
        return await m.reply("Сначала нажмите «Взять пользователя».")
    if d["admin_id"] != adm["id"] and not await is_main(m.from_user):
        return await m.reply("Этого пользователя ведёт другой админ.")
    try:
        sent = await relay_copy(m, d["user_id"])  # без плашки «переслано»
    except TelegramForbiddenError:
        await db.execute("UPDATE users SET alive=0 WHERE user_id=?", d["user_id"])
        return await m.reply("Пользователь заблокировал бота.")
    except Exception as e:  # noqa
        await log("system", "relay_error", f"{type(e).__name__}: {e}")
        return await m.reply(f"⚠️ Не удалось отправить пользователю ({esc(type(e).__name__)}: {esc(e)})")
    await relay_add(m.chat.id, m.message_id, d["user_id"], sent.message_id, d["id"])
    await ev("reply", adm["id"], d["id"])
    if await flag("f_react"):
        await safe(m.react([ReactionTypeEmoji(emoji="👍")]))
    await log(m.from_user.id, "admin_reply", f"dialog {d['id']}")


# ═════════════════════ ПАНЕЛЬ ГЛАВНОГО АДМИНА ═════════════════════
BACK = [btn("⬅️ Меню", "p:menu")]


def menu_kb():
    return kb([
        [btn("📝 Приветствие", "p:welcome"), btn("💬 Тексты", "p:texts")],
        [btn("👮 Админы", "p:admins"), btn("🏢 Группы", "p:groups")],
        [btn("📣 Рассылка", "p:bc"), btn("📊 Аналитика", "p:stats")],
        [btn("📜 Логи", "p:logs"), btn("🚫 Баны", "p:bans")],
        [btn("🔧 Настройки", "p:settings")],
    ])


async def show(c: CallbackQuery, text, markup=None):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await c.message.answer(text, reply_markup=markup)
    await c.answer()


@panel.message(Command("admin"))
async def p_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⚙️ <b>Панель главного админа</b>", reply_markup=menu_kb())


@panel.message(Command("cancel"))
async def p_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=menu_kb())


@panel.callback_query(F.data == "p:menu")
async def p_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "⚙️ <b>Панель главного админа</b>", menu_kb())


# ── Приветствие ──
@panel.callback_query(F.data == "p:welcome")
async def p_welcome(c: CallbackQuery):
    await show(c, "📝 <b>Приветствие бота</b>", kb([
        [btn("✏️ Текст", "p:w_text"), btn("🖼 Картинка", "p:w_photo")],
        [btn("🗑 Убрать картинку", "p:w_delphoto"), btn("👁 Предпросмотр", "p:w_prev")],
        [btn("📜 Правила бота", "p:t:rules_users"), btn("📜 Правила админов", "p:t:rules_admins")], BACK]))


@panel.callback_query(F.data == "p:w_text")
async def w_text(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.w_text)
    await c.message.answer("Отправьте новый текст приветствия (форматирование и премиум-эмодзи сохранятся). /cancel — отмена")
    await c.answer()


@panel.message(S.w_text, NOCMD)
async def w_text_save(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен именно текст.")
    await set_setting("welcome_text", m.html_text)
    await log(m.from_user.id, "welcome_text_changed")
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("👁 Предпросмотр", "p:w_prev")], BACK]))


@panel.callback_query(F.data == "p:w_photo")
async def w_photo(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.w_photo)
    await c.message.answer("Отправьте картинку для приветствия. /cancel — отмена")
    await c.answer()


@panel.message(S.w_photo, NOCMD)
async def w_photo_save(m: Message, state: FSMContext):
    if not m.photo:
        return await m.answer("Нужно отправить фото (не файлом).")
    await set_setting("welcome_photo", m.photo[-1].file_id)
    await log(m.from_user.id, "welcome_photo_changed")
    await state.clear()
    await m.answer("✅ Картинка сохранена.", reply_markup=kb([[btn("👁 Предпросмотр", "p:w_prev")], BACK]))


@panel.callback_query(F.data == "p:w_delphoto")
async def w_delphoto(c: CallbackQuery):
    await del_setting("welcome_photo")
    await log(c.from_user.id, "welcome_photo_removed")
    await c.answer("Картинка убрана", show_alert=True)


@panel.callback_query(F.data == "p:w_prev")
async def w_prev(c: CallbackQuery):
    await send_html(c.message.chat.id, await get_setting("welcome_text", DEFAULT_WELCOME),
                    await get_setting("welcome_photo"), start_kb())
    await c.answer()


# ── Тексты ──
@panel.callback_query(F.data == "p:texts")
async def p_texts(c: CallbackQuery):
    rows = [[btn(v[0], f"p:t:{k}")] for k, v in TEXT_KEYS.items()]
    await show(c, "💬 <b>Системные тексты</b>\nВыберите, какой изменить:", kb(rows + [BACK]))


@panel.callback_query(F.data.startswith("p:t:"))
async def p_text_pick(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[2]
    await state.set_state(S.text_edit)
    await state.update_data(key=key)
    cur = await txt(key)
    await c.message.answer(f"Текущий:\n\n{cur}\n\nОтправьте новый текст. /cancel — отмена")
    await c.answer()


@panel.message(S.text_edit, NOCMD)
async def p_text_save(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    key = (await state.get_data())["key"]
    await set_setting("t_" + key, m.html_text)
    await log(m.from_user.id, "text_changed", key)
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([BACK]))


# ── Админы ──
@panel.callback_query(F.data == "p:admins")
async def p_admins(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = await db.fetchall("SELECT * FROM admins ORDER BY level DESC, id")
    k = [[btn(f"{'⭐' if a['level'] == 2 else '👤'}{'' if a['active'] else '⏸'} {a['tag']} · {a['gender']}",
              f"p:a:{a['id']}")] for a in rows]
    k.append([btn("➕ Админ", "p:addadm:1"), btn("⭐ Главный админ", "p:addadm:2")])
    k.append(BACK)
    await show(c, "👮 <b>Админы</b> (⭐ главные, ⏸ отключены)", kb(k))


async def admin_card(c: CallbackQuery, aid):
    a = await db.fetchone("SELECT * FROM admins WHERE id=?", aid)
    if not a:
        return await c.answer("Не найден", show_alert=True)
    ident = f"{a['user_id'] or 'ещё не заходил в бота'} / " + (f"@{a['username']}" if a["username"] else "без юзернейма")
    ru = await rest_until(aid)
    rest_txt = f" · 💤 отдых до {ru[11:16]} UTC" if ru else ""
    text = (f"<b>{esc(a['tag'])}</b>\nПол: {esc(a['gender'])}\nУровень: {'главный админ' if a['level'] == 2 else 'админ'}\n"
            f"Статус: {'активен' if a['active'] else 'отключён'}{rest_txt}\nID/юзернейм: <code>{esc(ident)}</code>\n\n"
            f"Приветствие:\n{a['greeting']}")
    k = [[btn("✏️ Тег", f"p:ae:{aid}:tag"), btn("✏️ Пол", f"p:ae:{aid}:gender"), btn("✏️ Привет.", f"p:ae:{aid}:greeting")],
         [btn("🔁 Уровень", f"p:al:{aid}"), btn("⏯ Вкл/выкл", f"p:at:{aid}")],
         [btn("💤 Отдых / вернуть", f"p:ar:{aid}")],
         [btn("🗑 Удалить", f"p:ad:{aid}")], [btn("⬅️ К списку", "p:admins")]]
    await show(c, text, kb(k))


@panel.callback_query(F.data.startswith("p:a:"))
async def p_admin_open(c: CallbackQuery):
    await admin_card(c, int(c.data.split(":")[2]))


@panel.callback_query(F.data.startswith("p:ae:"))
async def p_admin_edit(c: CallbackQuery, state: FSMContext):
    _, _, aid, field = c.data.split(":")
    if field == "gender":
        return await show(c, "Выберите пол:", kb([[btn(v, f"p:ag:{aid}:{k}") for k, v in GENDER.items()]]))
    await state.set_state(S.edit_adm)
    await state.update_data(aid=int(aid), field=field)
    await c.message.answer("Отправьте новое значение. /cancel — отмена")
    await c.answer()


@panel.message(S.edit_adm, NOCMD)
async def p_admin_edit_save(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    d = await state.get_data()
    if d["field"] == "tag":
        val = norm_tag(m.text)
    else:
        val = m.html_text
    await db.execute(f"UPDATE admins SET {d['field']}=? WHERE id=?", val, d["aid"])
    await log(m.from_user.id, "admin_edited", f"{d['aid']} {d['field']}")
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("К админу", f"p:a:{d['aid']}")], BACK]))


@panel.callback_query(F.data.startswith("p:ag:"))
async def p_admin_gender(c: CallbackQuery):
    _, _, aid, g = c.data.split(":")
    await db.execute("UPDATE admins SET gender=? WHERE id=?", GENDER[g], int(aid))
    await admin_card(c, int(aid))


@panel.callback_query(F.data.startswith("p:al:"))
async def p_admin_level(c: CallbackQuery):
    aid = int(c.data.split(":")[2])
    a = await db.fetchone("SELECT * FROM admins WHERE id=?", aid)
    if a["user_id"] in OWNERS:
        return await c.answer("Владельца из MAIN_ADMIN_IDS менять нельзя", show_alert=True)
    await db.execute("UPDATE admins SET level=? WHERE id=?", 1 if a["level"] == 2 else 2, aid)
    await log(c.from_user.id, "admin_level", str(aid))
    await admin_card(c, aid)


@panel.callback_query(F.data.startswith("p:at:"))
async def p_admin_toggle(c: CallbackQuery):
    aid = int(c.data.split(":")[2])
    a = await db.fetchone("SELECT * FROM admins WHERE id=?", aid)
    if a["user_id"] in OWNERS:
        return await c.answer("Владельца отключать нельзя", show_alert=True)
    new = 0 if a["active"] else 1
    await db.execute("UPDATE admins SET active=? WHERE id=?", new, aid)
    if not new:
        await release_admin_dialogs(aid)
    await log(c.from_user.id, "admin_toggle", f"{aid} -> {new}")
    await admin_card(c, aid)


@panel.callback_query(F.data.startswith("p:ar:"))
async def p_admin_rest(c: CallbackQuery):
    aid = int(c.data.split(":")[2])
    a = await db.fetchone("SELECT * FROM admins WHERE id=?", aid)
    if not a:
        return await c.answer("Не найден", show_alert=True)
    if await rest_until(aid):
        await end_rest(aid)
        await log(c.from_user.id, "rest_ended_by_main", str(aid))
    else:
        await start_rest(a, 60)
        await log(c.from_user.id, "rest_set_by_main", f"{aid} 60 min")
    await admin_card(c, aid)


@panel.callback_query(F.data.startswith("p:ad:"))
async def p_admin_del(c: CallbackQuery):
    aid = c.data.split(":")[2]
    await show(c, "Точно удалить админа?", kb([[btn("✅ Да, удалить", f"p:ady:{aid}"), btn("❌ Нет", f"p:a:{aid}")]]))


@panel.callback_query(F.data.startswith("p:ady:"))
async def p_admin_del_ok(c: CallbackQuery):
    aid = int(c.data.split(":")[2])
    a = await db.fetchone("SELECT * FROM admins WHERE id=?", aid)
    if a and a["user_id"] in OWNERS:
        return await c.answer("Владельца удалить нельзя", show_alert=True)
    await release_admin_dialogs(aid)
    await db.execute("DELETE FROM admins WHERE id=?", aid)
    await log(c.from_user.id, "admin_deleted", str(aid))
    await p_admins(c, None)  # type: ignore


def norm_tag(t):
    t = re.sub(r"\s+", "_", t.strip())
    return t if t.startswith("#") else "#" + t


@panel.callback_query(F.data.startswith("p:addadm:"))
async def add_adm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddAdm.ident)
    await state.update_data(level=int(c.data.split(":")[2]))
    await c.message.answer("➕ <b>Новый админ</b>\n\n1/4 Отправьте <b>@юзернейм</b> или <b>числовой ID</b>.\n/cancel — отмена")
    await c.answer()


@panel.message(AddAdm.ident, NOCMD)
async def add_ident(m: Message, state: FSMContext):
    t = (m.text or "").strip().lstrip("@")
    if t.isdigit():
        uid, uname = int(t), None
        ex = await db.fetchone("SELECT id FROM admins WHERE user_id=?", uid)
    elif re.fullmatch(r"\w{4,32}", t):
        uid, uname = None, t.lower()
        ex = await db.fetchone("SELECT id FROM admins WHERE lower(username)=?", uname)
    else:
        return await m.answer("Не похоже на юзернейм или ID, попробуйте ещё раз.")
    if ex:
        return await m.answer("Такой админ уже есть.")
    await state.update_data(user_id=uid, username=uname)
    await state.set_state(AddAdm.gender)
    await m.answer("2/4 Пол:", reply_markup=kb([[btn(v, f"p:ng:{k}") for k, v in GENDER.items()]]))


@panel.callback_query(AddAdm.gender, F.data.startswith("p:ng:"))
async def add_gender(c: CallbackQuery, state: FSMContext):
    await state.update_data(gender=GENDER[c.data.split(":")[2]])
    await state.set_state(AddAdm.tag)
    await c.message.answer("3/4 Тег, например <code>#фина</code>:")
    await c.answer()


@panel.message(AddAdm.tag, NOCMD)
async def add_tag(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    await state.update_data(tag=norm_tag(m.text))
    await state.set_state(AddAdm.greet)
    await m.answer("4/4 Приветствие админа (его получит пользователь, когда админ возьмёт его). "
                   "Можно с форматированием и премиум-эмодзи:")


@panel.message(AddAdm.greet, NOCMD)
async def add_greet(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    d = await state.get_data()
    await db.execute("INSERT INTO admins(user_id,username,gender,tag,greeting,level,active) VALUES(?,?,?,?,?,?,1)",
                     d["user_id"], d["username"], d["gender"], d["tag"], m.html_text, d["level"])
    await log(m.from_user.id, "admin_added", f"{d['user_id'] or d['username']} {d['tag']} lvl{d['level']}")
    await state.clear()
    note = ""
    if d["user_id"]:
        ok = await safe(bot.send_message(d["user_id"], "🎉 Вас назначили админом «Голоса в пустоте». Загляните в группу поддержки!"))
        if not ok:
            note = "\n⚠️ Не удалось написать ему: пусть сначала нажмёт /start у бота."
    else:
        note = "\nℹ️ Админ активируется, когда впервые напишет боту (любое сообщение / /start)."
    await m.answer(f"✅ Админ {esc(d['tag'])} добавлен.{note}", reply_markup=kb([[btn("👮 Админы", "p:admins")], BACK]))


# ── Группы ──
@panel.callback_query(F.data == "p:groups")
async def p_groups(c: CallbackQuery):
    cur = await main_group()
    rows = await db.fetchall("SELECT * FROM groups")
    k = [[btn(("⭐ " if g["chat_id"] == cur else "") + (g["title"] or str(g["chat_id"])), f"p:gs:{g['chat_id']}"),
          btn("🗑", f"p:gd:{g['chat_id']}")] for g in rows]
    await show(c, "🏢 <b>Группы</b>\nДобавить: добавьте бота в группу (админом с правом закреплять) "
                  "и отправьте там <code>/bind</code>.\nТап по названию — сделать активной (⭐).", kb(k + [BACK]))


@panel.callback_query(F.data.startswith("p:gs:"))
async def p_group_set(c: CallbackQuery):
    await set_setting("main_group", c.data.split(":", 2)[2])
    await log(c.from_user.id, "main_group_set", c.data.split(":", 2)[2])
    await p_groups(c)


@panel.callback_query(F.data.startswith("p:gd:"))
async def p_group_del(c: CallbackQuery):
    gid = c.data.split(":", 2)[2]
    await db.execute("DELETE FROM groups WHERE chat_id=?", int(gid))
    if await get_setting("main_group") == gid:
        await del_setting("main_group")
    await log(c.from_user.id, "group_deleted", gid)
    await p_groups(c)


# ── Рассылка ──
@panel.callback_query(F.data == "p:bc")
async def p_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.bc)
    await c.message.answer("📣 Отправьте сообщение для рассылки — любое: текст, фото, видео, стикер, голосовое, "
                           "премиум-эмодзи. /cancel — отмена")
    await c.answer()


@panel.message(S.bc, NOCMD)
async def p_bc_got(m: Message, state: FSMContext):
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    n = (await db.fetchone("SELECT COUNT(*) c FROM users WHERE banned=0 AND alive=1"))["c"]
    await m.answer(f"☝️ Так увидят получатели. Отправить <b>{n}</b> пользователям?",
                   reply_markup=kb([[btn("✅ Отправить", "p:bcgo"), btn("❌ Отмена", "p:menu")]]))


@panel.callback_query(F.data == "p:bcgo")
async def p_bc_go(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    if not d.get("src_msg"):
        return await c.answer("Нет сообщения", show_alert=True)
    uids = [x["user_id"] for x in await db.fetchall("SELECT user_id FROM users WHERE banned=0 AND alive=1")]
    await c.message.edit_text(f"⏳ Рассылка запущена ({len(uids)} получателей)...")
    await log(c.from_user.id, "broadcast_start", f"{len(uids)} users")
    asyncio.create_task(do_broadcast(c.message.chat.id, d["src_chat"], d["src_msg"], uids, c.from_user.id))
    await c.answer()


async def do_broadcast(admin_chat, src_chat, src_msg, uids, actor):
    ok = fail = 0
    for uid in uids:
        for attempt in range(2):
            try:
                await bot.copy_message(uid, src_chat, src_msg)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await db.execute("UPDATE users SET alive=0 WHERE user_id=?", uid)
                fail += 1
                break
            except Exception:  # noqa
                fail += 1
                break
        await asyncio.sleep(0.05)
    await log(actor, "broadcast_done", f"ok={ok} fail={fail}")
    await safe(bot.send_message(admin_chat, f"📣 Рассылка завершена.\n✅ Доставлено: {ok}\n❌ Не доставлено: {fail}"))


# ── Настройки ──
SETTING_KEYS = ("f_label", "f_react", "f_remind", "f_antispam", "f_userdel")


async def settings_kb():
    f = {k: await flag(k) for k in SETTING_KEYS}
    onoff = lambda v: "вкл" if v else "выкл"  # noqa
    return kb([
        [btn(f"Метка в группе: {'@юзернейм' if f['f_label'] else '#тег'}", "p:sf:f_label")],
        [btn(f"Реакция 👍 на ответы админов: {onoff(f['f_react'])}", "p:sf:f_react")],
        [btn(f"Напоминания о неразобранных: {onoff(f['f_remind'])}", "p:sf:f_remind")],
        [btn(f"Автобан за спам: {onoff(f['f_antispam'])}", "p:sf:f_antispam")],
        [btn(f"Удаление сообщений пользователем: {onoff(f['f_userdel'])}", "p:sf:f_userdel")], BACK])


SETTINGS_TEXT = ("🔧 <b>Настройки</b>\n\n"
                 "• <b>Метка в группе</b> — после сообщения пользователя бот пишет «Ведёт: @юзернейм» или тег. "
                 "Видно только в группе.\n"
                 "• <b>Реакция</b> — 👍 на ответ админа, когда он ушёл пользователю.\n"
                 "• <b>Напоминания</b> — если обращение не взяли за 10 минут, бот напомнит в группе.\n"
                 f"• <b>Автобан</b> — больше {SPAM_N} сообщений за {SPAM_SEC} с = блокировка.\n"
                 "• <b>Удаление</b> — разрешает пользователю /del на свои сообщения.")


@panel.callback_query(F.data == "p:settings")
async def p_settings(c: CallbackQuery):
    await show(c, SETTINGS_TEXT, await settings_kb())


@panel.callback_query(F.data.startswith("p:sf:"))
async def p_setting_toggle(c: CallbackQuery):
    key = c.data.split(":")[2]
    if key not in SETTING_KEYS:
        return await c.answer()
    new = "0" if await flag(key) else "1"
    await set_setting(key, new)
    await log(c.from_user.id, "setting_toggled", f"{key}={new}")
    await show(c, SETTINGS_TEXT, await settings_kb())


# ── Аналитика ──
async def cnt(sql, *a):
    return (await db.fetchone(sql, *a))["c"]


def ago(days=0):
    if days == 0:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


STATS_NAV = [[btn("👮 По админам", "p:st_adm"), btn("📈 По дням", "p:st_days")],
             [btn("🔄 Обновить", "p:stats")], BACK]


@panel.callback_query(F.data == "p:stats")
async def p_stats(c: CallbackQuery):
    tot = await cnt("SELECT COUNT(*) c FROM users")
    alive = await cnt("SELECT COUNT(*) c FROM users WHERE alive=1")
    banned = await cnt("SELECT COUNT(*) c FROM users WHERE banned=1")
    new_u = [await cnt("SELECT COUNT(*) c FROM users WHERE first_seen>=?", ago(d)) for d in (0, 7, 30)]
    act_u = [await cnt("SELECT COUNT(*) c FROM users WHERE last_seen>=?", ago(d)) for d in (0, 7, 30)]
    repeat = await cnt("SELECT COUNT(*) c FROM (SELECT user_id FROM dialogs GROUP BY user_id HAVING COUNT(*)>1) t")

    ds = {x["status"]: x["c"] for x in await db.fetchall("SELECT status, COUNT(*) c FROM dialogs GROUP BY status")}
    d_tot = sum(ds.values())
    new_d = [await cnt("SELECT COUNT(*) c FROM dialogs WHERE created>=?", ago(d)) for d in (0, 7, 30)]
    msgs = [await cnt("SELECT COUNT(*) c FROM logs WHERE action='user_message' AND ts>=?", ago(d)) for d in (0, 7, 30)]
    reps = [await cnt("SELECT COUNT(*) c FROM logs WHERE action='admin_reply' AND ts>=?", ago(d)) for d in (0, 7, 30)]
    msgs_all = await cnt("SELECT COUNT(*) c FROM logs WHERE action='user_message'")
    reps_all = await cnt("SELECT COUNT(*) c FROM logs WHERE action='admin_reply'")
    avg = round(msgs_all / d_tot, 1) if d_tot else 0

    adm_all = await cnt("SELECT COUNT(*) c FROM admins")
    adm_act = await cnt("SELECT COUNT(*) c FROM admins WHERE active=1")
    adm_main = await cnt("SELECT COUNT(*) c FROM admins WHERE level=2 AND active=1")
    groups = await cnt("SELECT COUNT(*) c FROM groups")
    bcs = await cnt("SELECT COUNT(*) c FROM logs WHERE action='broadcast_done'")

    def row(label, v):
        return f"{label}: {v[0]} / {v[1]} / {v[2]}"

    text = (
        "📊 <b>Аналитика бота</b>\n\n"
        "👥 <b>Пользователи</b>\n"
        f"Всего: <b>{tot}</b>\n"
        f"Доступны для рассылки: {alive}\n"
        f"Заблокировали бота: {tot - alive}\n"
        f"Забанены: {banned}\n"
        f"Обращались повторно: {repeat}\n"
        "<i>сегодня / 7 дней / 30 дней</i>\n"
        f"{row('Новые', new_u)}\n"
        f"{row('Активные', act_u)}\n\n"
        "💬 <b>Диалоги</b>\n"
        f"Всего: <b>{d_tot}</b> · ждут: {ds.get('pending', 0)} · идут: {ds.get('active', 0)} · "
        f"закрыто: {ds.get('closed', 0)}\n"
        f"{row('Новые', new_d)}\n\n"
        "✉️ <b>Сообщения</b>\n"
        f"От пользователей: {msgs_all} · ответов админов: {reps_all}\n"
        f"{row('От пользователей', msgs)}\n"
        f"{row('Ответы админов', reps)}\n"
        f"В среднем на диалог: {avg}\n\n"
        "⚙️ <b>Система</b>\n"
        f"Админов: {adm_act}/{adm_all} активно (главных: {adm_main})\n"
        f"Групп: {groups} · рассылок: {bcs}\n"
        "<i>Счётчики сообщений ведутся с момента запуска логов.</i>"
    )
    await show(c, text, kb(STATS_NAV))


@panel.callback_query(F.data == "p:st_adm")
async def p_st_adm(c: CallbackQuery):
    admins = await db.fetchall("SELECT * FROM admins ORDER BY level DESC, id")
    taken = {x["admin_id"]: x["c"] for x in await db.fetchall(
        "SELECT admin_id, COUNT(*) c FROM dialogs WHERE admin_id IS NOT NULL GROUP BY admin_id")}
    live = {x["admin_id"]: x["c"] for x in await db.fetchall(
        "SELECT admin_id, COUNT(*) c FROM dialogs WHERE status='active' AND admin_id IS NOT NULL GROUP BY admin_id")}
    rep = {x["actor"]: x["c"] for x in await db.fetchall(
        "SELECT actor, COUNT(*) c FROM logs WHERE action='admin_reply' GROUP BY actor")}
    rep7 = {x["actor"]: x["c"] for x in await db.fetchall(
        "SELECT actor, COUNT(*) c FROM logs WHERE action='admin_reply' AND ts>=? GROUP BY actor", ago(7))}
    items = []
    for a in admins:
        key = str(a["user_id"]) if a["user_id"] else "-"
        items.append((rep.get(key, 0), a, taken.get(a["id"], 0), live.get(a["id"], 0), rep7.get(key, 0)))
    items.sort(key=lambda x: -x[0])
    lines = []
    for r_all, a, t, lv, r7 in items:
        mark = ("⭐" if a["level"] == 2 else "👤") + ("" if a["active"] else "⏸")
        lines.append(f"{mark} <b>{esc(a['tag'])}</b>\n   диалогов: {t} (сейчас {lv}) · ответов: {r_all} (за 7 дн: {r7})")
    await show(c, "👮 <b>Аналитика по админам</b>\n<i>рейтинг по числу ответов</i>\n\n" + ("\n".join(lines) or "Админов нет"),
               kb([[btn("⬅️ К аналитике", "p:stats")], BACK]))


@panel.callback_query(F.data == "p:st_days")
async def p_st_days(c: CallbackQuery):
    data = []
    for i in range(6, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        lo, hi = day + " 00:00:00", day + " 23:59:59"
        u = await cnt("SELECT COUNT(*) c FROM users WHERE first_seen>=? AND first_seen<=?", lo, hi)
        d = await cnt("SELECT COUNT(*) c FROM dialogs WHERE created>=? AND created<=?", lo, hi)
        m = await cnt("SELECT COUNT(*) c FROM logs WHERE action='user_message' AND ts>=? AND ts<=?", lo, hi)
        data.append((day[5:], u, d, m))
    mx = max([x[3] for x in data] + [1])
    lines = [f"{day}  👤{u:<3}💬{d:<3}✉️{m:<4}{'█' * round(m / mx * 8)}" for day, u, d, m in data]
    await show(c, "📈 <b>Последние 7 дней (UTC)</b>\n👤 новые пользователи · 💬 новые диалоги · ✉️ сообщения\n\n"
                  "<pre>" + "\n".join(lines) + "</pre>",
               kb([[btn("⬅️ К аналитике", "p:stats")], BACK]))


# ── Логи, баны ──
def fmt_logs(rows):
    return "\n".join(f"{x['ts'][5:16]} | {x['actor']} | {x['action']} | {x['details']}" for x in rows)


@panel.callback_query(F.data == "p:logs")
async def p_logs(c: CallbackQuery):
    rows = await db.fetchall("SELECT * FROM logs ORDER BY id DESC LIMIT 20")
    body = esc(fmt_logs(rows)) or "пусто"
    await show(c, f"📜 <b>Последние события</b>\n\n<pre>{body}</pre>",
               kb([[btn("📥 Скачать файл", "p:logfile")], BACK]))


@panel.callback_query(F.data == "p:logfile")
async def p_logfile(c: CallbackQuery):
    rows = await db.fetchall("SELECT * FROM logs ORDER BY id DESC LIMIT 3000")
    data = fmt_logs(rows).encode()
    await c.message.answer_document(BufferedInputFile(data, "logs.txt"))
    await c.answer()


@panel.callback_query(F.data == "p:bans")
async def p_bans(c: CallbackQuery):
    rows = await db.fetchall("SELECT user_id, name FROM users WHERE banned=1 LIMIT 30")
    k = [[btn(f"♻️ Разбанить {x['user_id']}", f"p:ub:{x['user_id']}")] for x in rows]
    await show(c, "🚫 <b>Забаненные</b>" + ("" if rows else "\n\nСписок пуст."), kb(k + [BACK]))


@panel.callback_query(F.data.startswith("p:ub:"))
async def p_unban(c: CallbackQuery):
    uid = int(c.data.split(":")[2])
    await db.execute("UPDATE users SET banned=0 WHERE user_id=?", uid)
    await log(c.from_user.id, "unban", str(uid))
    await p_bans(c)


# ───────────────────────── Веб-сервер и «будилка» ─────────────────────────
async def health(_):
    return web.Response(text="Голос в пустоте: OK")


async def keepalive():
    if not SELF_URL:
        logging.warning("SELF_URL/RENDER_EXTERNAL_URL не задан — самопинг отключён")
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                async with s.get(SELF_URL.rstrip("/") + "/health") as resp:
                    logging.info("keepalive ping: %s", resp.status)
            except Exception as e:  # noqa
                logging.warning("keepalive error: %s", e)


async def reminder():
    mins = int(os.getenv("REMIND_MIN", "10"))
    while True:
        await asyncio.sleep(60)
        try:
            await end_expired_rests()
            if not await flag("f_remind"):
                continue
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
            for d in await db.fetchall("SELECT * FROM dialogs WHERE status='pending' AND created<=?", cutoff):
                if await cnt("SELECT COUNT(*) c FROM events WHERE kind='remind' AND dialog_id=?", d["id"]):
                    continue
                await ev("remind", None, d["id"])
                rp = (ReplyParameters(message_id=d["card_msg_id"], allow_sending_without_reply=True)
                      if d["card_msg_id"] else None)
                await safe(bot.send_message(d["group_id"], f"⏰ Обращение #{d['id']} ждёт слушателя больше {mins} мин. "
                                                           "Кто возьмёт?", reply_parameters=rp))
        except Exception as e:  # noqa
            logging.warning("reminder error: %s", e)


async def main():
    await db.init()
    await ensure_owners()
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    asyncio.create_task(keepalive())
    asyncio.create_task(reminder())

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(panel, r)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
