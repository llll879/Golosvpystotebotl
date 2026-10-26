# bot_part08.py — часть 8 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
# ═════════════════ АНКЕТА «СТАТЬ АДМИНОМ» ═════════════════
async def get_questions():
    raw = await get_setting("apply_questions", "")
    qs = [x.strip() for x in raw.split("\n") if x.strip()]
    return qs or list(DEFAULT_QUESTIONS)


def form_text(qs, answers):
    return "\n\n".join(f"<b>{esc(q)}</b>\n{esc(a)}" for q, a in zip(qs, answers))


def app_card_text(app, pairs, footer=""):
    who = f"{esc(app['name'])}" + (f" · @{esc(app['username'])}" if app["username"] else "") + \
          f" · ID <code>{app['user_id']}</code>"
    body = "\n\n".join(f"<b>{esc(q)}</b>\n{esc(a)}" for q, a in pairs)
    return f"📝 <b>Анкета #{app['id']}</b>\n👤 {who}\n\n{body}" + (f"\n\n{footer}" if footer else "")


@r.callback_query(F.data == "apply")
async def apply_open(c: CallbackQuery, state: FSMContext):
    u = await touch_user(c.from_user)
    if u["banned"]:
        return await c.answer("Доступ ограничен", show_alert=True)
    if not await flag("f_apply"):
        return await c.answer("Приём анкет сейчас закрыт. Загляните позже 💜", show_alert=True)
    if await get_admin(c.from_user):
        return await c.answer("Вы уже в команде 💜", show_alert=True)
    last = await db.fetchone("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 1", c.from_user.id)
    if last:
        if last["status"] == "pending":
            return await c.answer("Ваша анкета уже на рассмотрении — ждите решения 💜", show_alert=True)
        if last["status"] == "accepted":
            return await c.answer("Ваша анкета уже принята 💜", show_alert=True)
        if last["status"] == "rejected" and last["decided_at"]:
            passed = (datetime.now(timezone.utc) - _parse(last["decided_at"])).total_seconds() / 3600
            if passed < APPLY_COOLDOWN_H:
                return await c.answer(f"Повторно подать анкету можно через {int(APPLY_COOLDOWN_H - passed) + 1} ч.",
                                      show_alert=True)
    await state.clear()
    await c.message.answer(await txt("apply_intro"), reply_markup=kb([
        [btn("▶️ Заполнить анкету", "apply_go")], [btn("❌ Отмена", "apply_cancel")]]))
    await c.answer()


@r.callback_query(F.data == "apply_go")
async def apply_go(c: CallbackQuery, state: FSMContext):
    qs = await get_questions()
    await state.set_state(ApplyS.q)
    await state.update_data(qs=qs, a=[])
    await c.message.answer(f"📝 <b>Вопрос 1/{len(qs)}</b>\n\n{esc(qs[0])}\n\n<i>Отмена — /cancel</i>")
    await c.answer()


@r.message(ApplyS.q, PRIV, NOCMD)
async def apply_answer(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текстовый ответ.")
    if len(m.text) > 1000:
        return await m.answer("Слишком длинно — уложитесь в 1000 символов.")
    d = await state.get_data()
    qs, a = d["qs"], d["a"] + [m.text.strip()]
    if len(a) < len(qs):
        await state.update_data(a=a)
        return await m.answer(f"📝 <b>Вопрос {len(a) + 1}/{len(qs)}</b>\n\n{esc(qs[len(a)])}")
    await state.update_data(a=a)
    await state.set_state(ApplyS.confirm)
    await m.answer("📋 <b>Проверьте анкету перед отправкой</b>\n\n" + form_text(qs, a), reply_markup=kb([
        [btn("✅ Отправить", "apply_send")], [btn("🔁 Заново", "apply_go"), btn("❌ Отмена", "apply_cancel")]]))


@r.callback_query(ApplyS.confirm, F.data == "apply_send")
async def apply_send(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    gid = await main_group()
    if not gid:
        return await c.answer(await txt("no_group"), show_alert=True)
    pairs = list(zip(d["qs"], d["a"]))
    await db.execute("INSERT INTO applications(user_id,username,name,answers,status,created,group_id) "
                     "VALUES(?,?,?,?,?,?,?)", c.from_user.id, c.from_user.username, c.from_user.full_name,
                     json.dumps(pairs, ensure_ascii=False), "pending", now(), gid)
    app = await db.fetchone("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 1", c.from_user.id)
    try:
        card = await bot.send_message(gid, app_card_text(app, pairs), reply_markup=kb([[
            btn("✅ Принять", f"apply_ok:{app['id']}"), btn("❌ Отклонить", f"apply_no:{app['id']}")]]))
    except Exception as e:  # noqa
        await db.execute("UPDATE applications SET status='error' WHERE id=?", app["id"])
        await log("system", "apply_send_failed", str(e))
        return await c.answer("Не удалось отправить анкету, попробуйте позже", show_alert=True)
    await db.execute("UPDATE applications SET card_msg_id=? WHERE id=?", card.message_id, app["id"])
    await log(c.from_user.id, "apply_sent", f"app {app['id']}")
    await state.clear()
    await c.message.answer(await txt("apply_sent"))
    await c.answer()


@r.callback_query(F.data == "apply_cancel")
async def apply_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Хорошо, анкета отменена. Вы всегда можете вернуться 💜")
    await c.answer()


@r.message(Command("cancel"), PRIV)
async def user_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.")


@r.callback_query(F.data.startswith("apply_ok:") | F.data.startswith("apply_no:") | F.data.startswith("apply_adm:"))
async def apply_nomain(c: CallbackQuery):
    await c.answer("Эти кнопки доступны только главным админам.", show_alert=True)


# ───────── Реакции (в обе стороны) ─────────
async def relay_by_src(chat, msg):
    return await db.fetchone("SELECT * FROM relay WHERE src_chat=? AND src_msg=?", chat, msg)


async def relay_by_dst(chat, msg):
    return await db.fetchone("SELECT * FROM relay WHERE dst_chat=? AND dst_msg=?", chat, msg)


async def mirror_reaction(chat_id, msg_id, reactions, restore_thumb=False):
    """Ставит от имени бота ту же реакцию на соответствующее сообщение (личность реагирующего не раскрывается)."""
    pick = None
    if reactions:
        x = reactions[0]
        pick = x.emoji if isinstance(x, ReactionTypeEmoji) else "❤"  # премиум/платные реакции → ❤
    elif restore_thumb and await flag("f_react"):
        pick = "👍"  # реакцию сняли — возвращаем отметку «доставлено»
    last = None
    for emo in ([pick, "❤"] if pick and pick != "❤" else [pick]):
        try:
            await bot.set_message_reaction(chat_id, msg_id, reaction=[ReactionTypeEmoji(emoji=emo)] if emo else [])
            return
        except TelegramRetryAfter:
            return
        except TelegramAPIError as e:
            last = e
    if last:
        await log("system", "reaction_failed", f"{type(last).__name__}: {last}")


@r.message_reaction()
async def on_reaction(e: MessageReactionUpdated):
    if not await flag("f_reactions"):
        return
    new = list(e.new_reaction or [])
    if e.chat.type == ChatType.PRIVATE:
        # реакция пользователя
        if not e.user:
            return
        u = await db.fetchone("SELECT banned FROM users WHERE user_id=?", e.user.id)
        if u and u["banned"]:
            return
        row = await relay_by_src(e.chat.id, e.message_id)   # на своё сообщение → на пересланное в группе
        if row:
            return await mirror_reaction(row["dst_chat"], row["dst_msg"], new)
        row = await relay_by_dst(e.chat.id, e.message_id)   # на ответ админа → на сообщение админа в группе
        if row:
            await mirror_reaction(row["src_chat"], row["src_msg"], new, restore_thumb=True)
        return
    if e.chat.type in GROUPS:
        # реакция админа на сообщение пользователя
        if not e.user:
            return
        adm = await get_admin(e.user)
        if not adm:
            return
        row = await relay_by_dst(e.chat.id, e.message_id)
        if not row:
            return
        d = await db.fetchone("SELECT * FROM dialogs WHERE id=?", row["dialog_id"])
        if not d:
            return
        if not await is_main(e.user) and not (d["status"] == "active" and d["admin_id"] == adm["id"]):
            return
        await mirror_reaction(row["src_chat"], row["src_msg"], new)


@r.message(Command("stop"), PRIV)
async def user_stop(m: Message):
    d = await db.fetchone("SELECT * FROM dialogs WHERE user_id=? AND status IN ('pending','active') "
                          "ORDER BY id DESC LIMIT 1", m.from_user.id)
    if not d:
        return await m.answer("Активного диалога нет 🙂")
    await close_dialog(d["id"])
    await safe(bot.send_message(d["group_id"], f"🔒 Пользователь завершил диалог #{d['id']}",
                                message_thread_id=await dthread(d["group_id"], d["user_id"])))
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
                                                        "Разбан: /unban реплаем или панель /admin → Баны.",
                                        message_thread_id=await dthread(dd["group_id"], dd["user_id"])))
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
    topic = None
    repost = False
    if await is_forum(d["group_id"]):  # чат с темами: у каждого пользователя своя тема
        try:
            topic = await ensure_topic(u, d["group_id"], "active" if adm else "pending",
                                       adm["tag"] if adm else None, refresh=new)
        except Exception as e:  # noqa
            await log("system", "topic_error", f"{type(e).__name__}: {e}")
    if topic:
        try:
            try:
                fm = await relay_copy(m, d["group_id"], thread_id=topic, reply_to=await reply_target_for_user(m))
            except TelegramBadRequest as e:
                if not any(x in str(e).lower() for x in ("thread", "topic")):
                    raise
                topic = await ensure_topic(u, d["group_id"], "active" if adm else "pending",
                                           adm["tag"] if adm else None, recreate=True)
                repost = True
                fm = await relay_copy(m, d["group_id"], thread_id=topic)
        except TelegramAPIError as e:
            await log("system", "forward_error", f"{type(e).__name__}: {e}")
            return await m.answer("⚠️ Не удалось доставить сообщение, попробуй позже.")
    else:
        try:
            fm = await bot.forward_message(d["group_id"], m.chat.id, m.message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            await log("system", "forward_error", str(e))
            return await m.answer("⚠️ Не удалось доставить сообщение, попробуй позже.")
    await gmap_add(d["group_id"], fm.message_id, d["id"])
    lm = None
    if adm:
        await ev("in", adm["id"], d["id"])
        if not topic:  # в теме метка «Ведёт» не нужна — тема и так принадлежит диалогу
            lab = await admin_label(adm)
            lm = await safe(bot.send_message(d["group_id"], f"👤 Ведёт: {lab}",
                                             reply_parameters=ReplyParameters(message_id=fm.message_id,
                                                                              allow_sending_without_reply=True)))
            if lm:
                await gmap_add(d["group_id"], lm.message_id, d["id"])
    elif new or repost:
        await post_card(d["id"], d["group_id"], f"🆕 Новое обращение #{d['id']}", reply_to=fm.message_id, thread=topic)
        if new:
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
    await cleanup_notice(did)
    await topic_status(row["user_id"], row["group_id"], "active", adm["tag"])
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
