# bot_part02.py — часть 2 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
# ───────── Пересылка без «переслано» + запасные способы ─────────
async def relay_copy(m: Message, chat_id, thread_id=None, reply_to=None):
    """Копирует сообщение. Если Telegram отказал (премиум-стикеры, кастомные эмодзи и т.п.) — пробует запасные способы.
    thread_id — тема форум-чата, reply_to — id сообщения, на которое нужно ответить."""
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    kw = {"message_thread_id": thread_id, "reply_parameters": rp}
    try:
        return await m.copy_to(chat_id, **kw)
    except TelegramForbiddenError:
        raise
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await m.copy_to(chat_id, **kw)
    except TelegramAPIError as e:
        low = str(e).lower()
        if thread_id and ("thread" in low or "topic" in low):
            raise  # тема удалена/закрыта — пусть вызывающий код разберётся
        await log("system", "copy_failed", f"{type(e).__name__}: {e}")
        if m.sticker:
            return await bot.send_sticker(chat_id, m.sticker.file_id, **kw)
        if m.text:
            ents = [x for x in (m.entities or []) if x.type != "custom_emoji"]
            return await bot.send_message(chat_id, m.text, entities=ents or None, parse_mode=None, **kw)
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


# ───────── Режим тем (форум-чаты) ─────────
_forum_cache: dict = {}


async def is_forum(chat_id):
    """Включён ли режим тем: чат — форум (с темами) и настройка не выключена."""
    if not await flag("f_topics"):
        return False
    v = _forum_cache.get(chat_id)
    if v and time.monotonic() - v[1] < 600:
        return v[0]
    try:
        val = bool(getattr(await bot.get_chat(chat_id), "is_forum", False))
    except Exception:  # noqa
        val = False
    _forum_cache[chat_id] = (val, time.monotonic())
    return val


def topic_link(chat_id, thread_id):
    g = str(chat_id)
    return f"https://t.me/c/{g[4:]}/{thread_id}" if g.startswith("-100") else None


def topic_title(u, status, tag=None):
    nick = ("@" + u["username"]) if u.get("username") else (u.get("name") or str(u["user_id"]))
    icon = {"pending": "🆕", "active": "💬", "closed": "🔒"}.get(status, "💬")
    return (f"{icon} {nick}" + (f" · {tag}" if tag else ""))[:128]


async def ensure_topic(u, chat_id, status="pending", tag=None, refresh=False, recreate=False):
    """Возвращает id темы пользователя (создаёт, если нет). refresh — переоткрыть и переименовать."""
    row = None if recreate else await db.fetchone("SELECT thread_id FROM topics WHERE user_id=? AND chat_id=?",
                                                  u["user_id"], chat_id)
    title = topic_title(u, status, tag)
    if row:
        tid = row["thread_id"]
        if not refresh:
            return tid
        try:
            await bot.reopen_forum_topic(chat_id, tid)
        except TelegramBadRequest as e:
            msg = str(e).upper()
            if "NOT_MODIFIED" not in msg and "NOT MODIFIED" not in msg:
                row = None  # тема удалена — создадим заново
        if row:
            await safe(bot.edit_forum_topic(chat_id, tid, name=title))
            return tid
    created = await bot.create_forum_topic(chat_id, title)
    tid = created.message_thread_id
    await db.execute("INSERT INTO topics(user_id,chat_id,thread_id) VALUES(?,?,?) "
                     "ON CONFLICT(user_id,chat_id) DO UPDATE SET thread_id=excluded.thread_id", u["user_id"], chat_id, tid)
    nick = ("@" + u["username"]) if u.get("username") else (u.get("name") or str(u["user_id"]))
    await safe(bot.send_message(
        chat_id, f"🧵 <b>Тема пользователя {esc(nick)}</b>\n\n"
                 "• Просто пишите в теме — сообщение уйдёт пользователю как есть.\n"
                 "• Ответ (reply) на сообщение — придёт пользователю как ответ на него.\n"
                 "• Команды: /close, /release, /info, /ban, /del. Текст с «/» в начале никому не уходит.",
        message_thread_id=tid))
    return tid


async def topic_status(user_id, group_id, status, tag=None):
    """Переименовывает тему под статус диалога (и закрывает её, если диалог закрыт)."""
    if not await is_forum(group_id):
        return
    tr = await db.fetchone("SELECT thread_id FROM topics WHERE user_id=? AND chat_id=?", user_id, group_id)
    u = await db.fetchone("SELECT * FROM users WHERE user_id=?", user_id)
    if not tr or not u:
        return
    await safe(bot.edit_forum_topic(group_id, tr["thread_id"], name=topic_title(u, status, tag)))
    if status == "closed":
        await safe(bot.close_forum_topic(group_id, tr["thread_id"]))


async def dthread(group_id, user_id):
    if not await is_forum(group_id):
        return None
    tr = await db.fetchone("SELECT thread_id FROM topics WHERE user_id=? AND chat_id=?", user_id, group_id)
    return tr["thread_id"] if tr else None


async def cleanup_notice(did):
    v = await get_setting(f"tn_{did}")
    if not v:
        return
    d = await db.fetchone("SELECT group_id FROM dialogs WHERE id=?", did)
    if d:
        await safe(bot.delete_message(d["group_id"], int(v)))
    await del_setting(f"tn_{did}")


async def user_side_msg(group_id, gid):
    """id сообщения в чате пользователя, соответствующего сообщению gid в теме (для ответов админа)."""
    row = await db.fetchone("SELECT src_msg FROM relay WHERE dst_chat=? AND dst_msg=?", group_id, gid)
    if row:
        return row["src_msg"]
    row = await db.fetchone("SELECT dst_msg FROM relay WHERE src_chat=? AND src_msg=?", group_id, gid)
    return row["dst_msg"] if row else None


async def reply_target_for_user(m: Message):
    """id сообщения в теме, на которое пользователь ответил в личке (или None)."""
    rt = m.reply_to_message
    if not rt:
        return None
    row = await db.fetchone("SELECT dst_msg FROM relay WHERE src_chat=? AND src_msg=?", m.chat.id, rt.message_id)
    if row:
        return row["dst_msg"]
    row = await db.fetchone("SELECT src_msg FROM relay WHERE dst_chat=? AND dst_msg=?", m.chat.id, rt.message_id)
    return row["src_msg"] if row else None


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


async def post_card(did, group_id, text, reply_to=None, thread=None):
    d0 = await db.fetchone("SELECT user_id FROM dialogs WHERE id=?", did)
    if thread is None and d0:
        thread = await dthread(group_id, d0["user_id"])
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    card = await bot.send_message(group_id, text, reply_markup=kb([[btn("🙋 Взять пользователя", f"take:{did}")]]),
                                  reply_parameters=rp, message_thread_id=thread)
    await pin(group_id, card.message_id)
    await db.execute("UPDATE dialogs SET card_msg_id=? WHERE id=?", card.message_id, did)
    await gmap_add(group_id, card.message_id, did)
    if thread:  # в «Общую» тему — уведомление с кнопкой «Взять» и ссылкой на тему
        await cleanup_notice(did)
        link = topic_link(group_id, thread)
        row = [btn("🙋 Взять", f"take:{did}")]
        if link:
            row.append(InlineKeyboardButton(text="🔗 Открыть тему", url=link))
        n = await safe(bot.send_message(group_id, f"🆕 Обращение #{did} ждёт слушателя", reply_markup=kb([row])))
        if n:
            await set_setting(f"tn_{did}", str(n.message_id))


async def release_dialog(did):
    d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", did)
    if not d or d["status"] == "closed":
        return
    await db.execute("UPDATE dialogs SET admin_id=NULL, status='pending' WHERE id=?", did)
    if d["card_msg_id"]:
        await unpin(d["group_id"], d["card_msg_id"])
    await topic_status(d["user_id"], d["group_id"], "pending")
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
    await cleanup_notice(did)
    await topic_status(d["user_id"], d["group_id"], "closed")


async def release_admin_dialogs(admin_id):
    rows = await db.fetchall("SELECT id FROM dialogs WHERE admin_id=? AND status='active'", admin_id)
    for r in rows:
        await safe(release_dialog(r["id"]))


async def dialog_from_reply(m: Message):
    if m.reply_to_message:
        mp = await db.fetchone("SELECT dialog_id FROM gmap WHERE chat_id=? AND msg_id=?",
                               m.chat.id, m.reply_to_message.message_id)
        if mp:
            return await db.fetchone("SELECT * FROM dialogs WHERE id=?", mp["dialog_id"])
    if m.is_topic_message and m.message_thread_id:  # режим тем: диалог определяется по теме
        tr = await db.fetchone("SELECT user_id FROM topics WHERE chat_id=? AND thread_id=?", m.chat.id, m.message_thread_id)
        if tr:
            return await db.fetchone("SELECT * FROM dialogs WHERE user_id=? ORDER BY id DESC LIMIT 1", tr["user_id"])
    return None


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
