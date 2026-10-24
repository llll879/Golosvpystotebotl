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
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import Command, CommandStart, Filter
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
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, details TEXT)
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
    await safe(bot.pin_chat_message(group_id, card.message_id, disable_notification=True))
    await db.execute("UPDATE dialogs SET card_msg_id=? WHERE id=?", card.message_id, did)
    await gmap_add(group_id, card.message_id, did)


async def release_dialog(did):
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", did)
    if not d or d["status"] == "closed":
        return
    await db.execute("UPDATE dialogs SET admin_id=NULL, status='pending' WHERE id=?", did)
    if d["card_msg_id"]:
        await safe(bot.unpin_chat_message(d["group_id"], d["card_msg_id"]))
    await post_card(did, d["group_id"], f"🔄 Обращение #{did} снова ждёт слушателя")


async def close_dialog(did):
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", did)
    if not d or d["status"] == "closed":
        return
    await db.execute("UPDATE dialogs SET status='closed' WHERE id=?", did)
    if d["card_msg_id"]:
        await safe(bot.unpin_chat_message(d["group_id"], d["card_msg_id"]))
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


class IsMain(Filter):
    async def __call__(self, event) -> bool:
        return await is_main(event.from_user)


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
    await send_html(m.chat.id, text, photo, kb([[btn("👥 Посмотреть админов", "adm_list")]]))


@r.callback_query(F.data == "adm_list")
async def adm_list(c: CallbackQuery):
    rows = await db.fetchall("SELECT tag, gender FROM admins WHERE active=1 ORDER BY id")
    if not rows:
        return await c.answer("Пока нет слушателей", show_alert=True)
    lines = [f"{esc(a['tag'])} · {esc(a['gender'])}" for a in rows]
    await c.message.answer("👥 <b>Наши слушатели:</b>\n\n" + "\n".join(lines))
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
    if adm:
        lm = await safe(bot.send_message(d["group_id"], f"👤 Ведёт: {esc(adm['tag'])}",
                                         reply_parameters=ReplyParameters(message_id=fm.message_id,
                                                                          allow_sending_without_reply=True)))
        if lm:
            await gmap_add(d["group_id"], lm.message_id, d["id"])
    elif new:
        await post_card(d["id"], d["group_id"], f"🆕 Новое обращение #{d['id']}", reply_to=fm.message_id)
        await m.answer(await txt("sent_ok"))
    await log(m.from_user.id, "user_message", f"dialog {d['id']}")


@r.callback_query(F.data.startswith("take:"))
async def take(c: CallbackQuery):
    adm = await get_admin(c.from_user)
    if not adm:
        return await c.answer("Только для админов", show_alert=True)
    did = int(c.data.split(":")[1])
    row = await db.fetchone("UPDATE dialogs SET admin_id=?, status='active' WHERE id=? AND status='pending' "
                            "RETURNING user_id, card_msg_id, group_id", adm["id"], did)
    if not row:
        return await c.answer("Уже взят или закрыт", show_alert=True)
    if row["card_msg_id"]:
        await safe(bot.unpin_chat_message(row["group_id"], row["card_msg_id"]))
        await safe(bot.edit_message_text(f"✅ Обращение #{did} · взял {esc(adm['tag'])}",
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
    await m.reply("✅ Группа привязана. Сделайте бота админом с правом закреплять сообщения. "
                  "Активную группу можно выбрать в /admin → Группы.")


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
    if not m.from_user or (m.text and m.text.startswith("/")):
        return
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
        await m.copy_to(d["user_id"])  # без плашки «переслано»
    except TelegramForbiddenError:
        await db.execute("UPDATE users SET alive=0 WHERE user_id=?", d["user_id"])
        return await m.reply("Пользователь заблокировал бота.")
    except TelegramBadRequest as e:
        return await m.reply(f"Не удалось отправить: {esc(e)}")
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
    ])


async def show(c: CallbackQuery, text, markup=None):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await c.message.answer(text, reply_markup=markup)
    await c.answer()


@panel.message(Command("admin"), PRIV)
async def p_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⚙️ <b>Панель главного админа</b>", reply_markup=menu_kb())


@panel.message(Command("cancel"), PRIV)
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
        [btn("🗑 Убрать картинку", "p:w_delphoto"), btn("👁 Предпросмотр", "p:w_prev")], BACK]))


@panel.callback_query(F.data == "p:w_text")
async def w_text(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.w_text)
    await c.message.answer("Отправьте новый текст приветствия (форматирование и премиум-эмодзи сохранятся). /cancel — отмена")
    await c.answer()


@panel.message(S.w_text, PRIV, NOCMD)
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


@panel.message(S.w_photo, PRIV, NOCMD)
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
                    await get_setting("welcome_photo"), kb([[btn("👥 Посмотреть админов", "adm_list")]]))
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


@panel.message(S.text_edit, PRIV, NOCMD)
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
    ident = a["user_id"] or ("@" + a["username"] + " (ещё не заходил в бота)")
    text = (f"<b>{esc(a['tag'])}</b>\nПол: {esc(a['gender'])}\nУровень: {'главный админ' if a['level'] == 2 else 'админ'}\n"
            f"Статус: {'активен' if a['active'] else 'отключён'}\nID/юзернейм: <code>{esc(ident)}</code>\n\n"
            f"Приветствие:\n{a['greeting']}")
    k = [[btn("✏️ Тег", f"p:ae:{aid}:tag"), btn("✏️ Пол", f"p:ae:{aid}:gender"), btn("✏️ Привет.", f"p:ae:{aid}:greeting")],
         [btn("🔁 Уровень", f"p:al:{aid}"), btn("⏯ Вкл/выкл", f"p:at:{aid}")],
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


@panel.message(S.edit_adm, PRIV, NOCMD)
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


@panel.message(AddAdm.ident, PRIV, NOCMD)
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


@panel.message(AddAdm.tag, PRIV, NOCMD)
async def add_tag(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    await state.update_data(tag=norm_tag(m.text))
    await state.set_state(AddAdm.greet)
    await m.answer("4/4 Приветствие админа (его получит пользователь, когда админ возьмёт его). "
                   "Можно с форматированием и премиум-эмодзи:")


@panel.message(AddAdm.greet, PRIV, NOCMD)
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


@panel.message(S.bc, PRIV, NOCMD)
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

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(panel, r)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
