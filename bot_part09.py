# bot_part09.py — часть 9 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
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
    _forum_cache.pop(m.chat.id, None)
    note = await rights_note(m.chat.id)
    tp = ("\n\n🧵 Это чат с темами: для каждого пользователя бот создаст отдельную тему (режим включается "
          "в /admin → Настройки).") if m.chat.is_forum else ""
    await m.reply("✅ Группа привязана. Активную группу можно выбрать в /admin → Группы." + tp
                  + (f"\n\n{note}" if note else ""))


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


@r.message(Command("close"), GRP, F.reply_to_message | F.is_topic_message)
async def g_close(m: Message):
    adm, d = await _cmd_guard(m)
    if not d:
        return
    await close_dialog(d["id"])
    await safe(bot.send_message(d["user_id"], await txt("closed")))
    await ev("close", adm["id"], d["id"])
    await log(m.from_user.id, "close", f"dialog {d['id']}")
    await m.reply(f"🔒 Диалог #{d['id']} закрыт.")


@r.message(Command("release"), GRP, F.reply_to_message | F.is_topic_message)
async def g_release(m: Message):
    adm, d = await _cmd_guard(m)
    if not d or d["status"] != "active":
        return
    await release_dialog(d["id"])
    await log(m.from_user.id, "release", f"dialog {d['id']}")


@r.message(Command("ban"), GRP, F.reply_to_message | F.is_topic_message)
async def g_ban(m: Message):
    adm, d = await _cmd_guard(m)
    if not d:
        return
    await db.execute("UPDATE users SET banned=1 WHERE user_id=?", d["user_id"])
    await close_dialog(d["id"])
    await log(m.from_user.id, "ban", f"user {d['user_id']}")
    await m.reply("🚫 Пользователь забанен (разбан — в панели /admin).")


# ───────── Сообщения админов в темах ─────────
def _has_content(m: Message):
    return any([m.text, m.caption, m.photo, m.video, m.voice, m.audio, m.document, m.animation, m.sticker,
                m.video_note, m.contact, m.location, m.venue, m.poll, m.dice])


@r.message(GRP, F.is_topic_message)
async def topic_msg(m: Message):
    """Админ пишет в теме пользователя: просто сообщение → уходит пользователю; reply → уходит как ответ."""
    if not m.from_user or m.from_user.is_bot or not _has_content(m):
        return
    if (m.text or m.caption or "").startswith("/"):
        return  # команды и заметки коллегам
    tr = await db.fetchone("SELECT user_id FROM topics WHERE chat_id=? AND thread_id=?", m.chat.id, m.message_thread_id)
    if not tr:
        return
    adm = await get_admin(m.from_user)
    if not adm:
        return
    d = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? ORDER BY id DESC LIMIT 1", tr["user_id"])
    if not d:
        return
    if not await is_main(m.from_user):
        if d["status"] == "closed":
            return await m.reply("Диалог закрыт. Если пользователь напишет снова, тема откроется заново.")
        if d["status"] == "pending":
            return await m.reply("Сначала нажмите «Взять пользователя».")
        if d["admin_id"] != adm["id"]:
            return await m.reply("Этого пользователя ведёт другой админ.")
    reply_to = None
    rt = m.reply_to_message
    if rt and not rt.forum_topic_created and rt.message_id != m.message_thread_id:
        reply_to = await user_side_msg(m.chat.id, rt.message_id)
    try:
        sent = await relay_copy(m, d["user_id"], reply_to=reply_to)
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
    await log(m.from_user.id, "admin_reply", f"dialog {d['id']} (в теме)")


@r.message(Command("cleantopics"))
async def cmd_cleantopics(m: Message, command: CommandObject):
    """Главный админ: удалить темы закрытых диалогов, открытых более N дней назад (по умолчанию 7)."""
    if not m.from_user or not await is_main(m.from_user):
        return
    days = int(command.args.strip()) if command.args and command.args.strip().isdigit() else 7
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for t in await db.fetchall("SELECT user_id, chat_id, thread_id FROM topics"):
        d = await db.fetchone("SELECT status, created FROM dialogs WHERE user_id=? ORDER BY id DESC LIMIT 1", t["user_id"])
        if d and d["status"] == "closed" and d["created"] <= cutoff:
            try:
                await bot.delete_forum_topic(t["chat_id"], t["thread_id"])
            except TelegramAPIError as e:
                await log("system", "topic_delete_failed", str(e))
                continue
            await db.execute("DELETE FROM topics WHERE user_id=? AND chat_id=?", t["user_id"], t["chat_id"])
            n += 1
    await log(m.from_user.id, "clean_topics", f"{n} тем, старше {days} дн.")
    await m.reply(f"🧹 Удалено тем: {n} (закрытые диалоги старше {days} дн.). Новое сообщение пользователя создаст тему заново.")


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
    if not await is_main(m.from_user):  # главные админы могут писать в любом диалоге и без взятия
        if d["status"] == "closed":
            return await m.reply("Диалог закрыт.")
        if d["status"] == "pending":
            return await m.reply("Сначала нажмите «Взять пользователя».")
        if d["admin_id"] != adm["id"]:
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
        [btn("🤖 ИИ", "p:ai"), btn("📋 Анкета", "p:apply")],
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
