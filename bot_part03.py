# bot_part03.py — часть 3 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
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


@r.message(Command("info"), F.reply_to_message | F.is_topic_message)
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
            "• /guide или «методичка» — полная методичка по боту\n"
            "• /ai вопрос — ИИ-помощник (знает всё про бота), /exit — выйти из ИИ-диалога")
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
    if await is_forum(row["dst_chat"]):  # режим тем: копия принадлежит боту — правим её напрямую
        try:
            if m.text:
                await bot.edit_message_text(m.html_text, chat_id=row["dst_chat"], message_id=row["dst_msg"])
                return await log(m.chat.id, "user_edit", f"dialog {d['id']} (в теме)")
            if m.caption is not None or m.photo or m.video or m.document or m.animation or m.audio or m.voice:
                await bot.edit_message_caption(chat_id=row["dst_chat"], message_id=row["dst_msg"],
                                               caption=m.html_text or "")
                return await log(m.chat.id, "user_edit", f"dialog {d['id']} (в теме)")
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
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
@r.message(Command("del", "delete"), GRP, F.reply_to_message | F.is_topic_message)
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
    await safe(bot.send_message(m.chat.id, "🗑 Сообщение удалено у пользователя.",
                                message_thread_id=m.message_thread_id if m.is_topic_message else None))


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
    await safe(bot.send_message(row["dst_chat"], f"🗑 Пользователь удалил своё сообщение (обращение #{row['dialog_id']}).",
                                message_thread_id=await dthread(row["dst_chat"], m.chat.id)))
    await log(m.chat.id, "user_delete", f"dialog {row['dialog_id']}")
    await m.answer("🗑 Сообщение удалено у слушателей. Из этого чата удалите его сами.")


# ───────── Методичка ─────────
GUIDE_ROOT = ("📖 <b>Методичка по боту «Голос в пустоте»</b>\n\n"
              "Здесь описано всё: как устроен бот, каждая команда и каждая кнопка.\nВыберите раздел:")
