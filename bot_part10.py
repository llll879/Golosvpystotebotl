# bot_part10.py — часть 10 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
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
    await m.answer(f"☝️ Так увидят получатели. Отправить <b>{n}</b> пользователям (≈ {int(n * 0.06) + 1} сек)?",
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
    asyncio.create_task(do_broadcast(c.message.chat.id, d["src_chat"], d["src_msg"], uids, c.from_user.id, c.message))
    await c.answer()


async def do_broadcast(admin_chat, src_chat, src_msg, uids, actor, status_msg=None):
    ok = fail = gone = 0
    total = len(uids)
    for i, uid in enumerate(uids, 1):
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
                gone += 1
                break
            except TelegramBadRequest as e:
                if any(x in str(e).lower() for x in ("chat not found", "deactivated", "user not found")):
                    await db.execute("UPDATE users SET alive=0 WHERE user_id=?", uid)
                    gone += 1
                fail += 1
                break
            except Exception:  # noqa
                fail += 1
                break
        if status_msg and i % 100 == 0:
            await safe(status_msg.edit_text(f"⏳ Рассылка: {i}/{total} (доставлено {ok}, не доставлено {fail})"))
        await asyncio.sleep(0.05)
    await log(actor, "broadcast_done", f"ok={ok} fail={fail}")
    extra = (f"\n🚫 Недоступны (заблокировали бота / аккаунт удалён / не запускали бота): {gone} — помечены "
             "и в следующих рассылках не участвуют.") if gone else ""
    await safe(bot.send_message(admin_chat, f"📣 Рассылка завершена.\n✅ Доставлено: {ok}\n❌ Не доставлено: {fail}{extra}"))


# ── ИИ: настройки (Gemini) ──
class AiCfg(StatesGroup):
    key = State()
    model_a = State()
    prompt = State()
    contact = State()


class ApplyCfg(StatesGroup):
    add = State()
    edit = State()


async def ai_menu():
    dbk, allk = await db_keys(), await get_keys()
    on = lambda v: "вкл ✅" if v else "выкл ⛔"  # noqa
    fa = await flag("f_ai_admin")
    extra = (await get_setting("ai_extra", "")).strip()
    text = ("🤖 <b>ИИ-помощник (Gemini)</b>\n\n"
            f"🔑 Ключей: <b>{len(allk)}</b> (в боте: {len(dbk)}, из окружения: {len(allk) - len(dbk)})\n"
            f"🧠 Модель: <code>{esc(await get_model('admin'))}</code>\n"
            f"🛡 ИИ для админов (/ai): {on(fa)}\n"
            f"👨‍💻 Контакт человека: {esc(await human_contact())}\n"
            f"📝 Доп. инструкции: {'заданы' if extra else 'нет'}\n\n"
            "Ключи берутся на aistudio.google.com и пробуются по очереди: если лимит одного исчерпан, "
            "бот возьмёт следующий.")
    k = kb([
        [btn("🔑 Добавить ключ", "p:ai_addkey"), btn("🗑 Ключи", "p:ai_keys")],
        [btn("🧠 Модель", "p:ai_ma")],
        [btn("📋 Доступные модели", "p:ai_models"), btn("🧪 Тест", "p:ai_test")],
        [btn(f"🛡 ИИ для админов: {on(fa)}", "p:aif:f_ai_admin")],
        [btn("📝 Доп. инструкции", "p:ai_prompt"), btn("👨‍💻 Контакт человека", "p:ai_contact")],
        BACK])
    return text, k


@panel.callback_query(F.data == "p:ai")
async def p_ai(c: CallbackQuery, state: FSMContext):
    await state.clear()
    t, k = await ai_menu()
    await show(c, t, k)


@panel.callback_query(F.data.startswith("p:aif:"))
async def p_ai_flag(c: CallbackQuery):
    key = c.data.split(":")[2]
    if key != "f_ai_admin":
        return await c.answer()
    new = "0" if await flag(key) else "1"
    await set_setting(key, new)
    await log(c.from_user.id, "setting_toggled", f"{key}={new}")
    t, k = await ai_menu()
    await show(c, t, k)


AI_BACK = kb([[btn("⬅️ К настройкам ИИ", "p:ai")]])


@panel.callback_query(F.data == "p:ai_addkey")
async def p_ai_addkey(c: CallbackQuery, state: FSMContext):
    await state.set_state(AiCfg.key)
    await c.message.answer("🔑 Отправьте API-ключ Gemini (можно несколько — через пробел или с новой строки). "
                           "Бот проверит ключ и <b>удалит ваше сообщение</b> (ему нужно право удалять сообщения; "
                           "в группе лучше сразу удалить его вручную, если бот не смог). Лучше добавлять ключи в личке.\n"
                           "/cancel — отмена")
    await c.answer()


@panel.message(AiCfg.key, NOCMD)
async def p_ai_key_save(m: Message, state: FSMContext):
    raw = m.text or ""
    await safe(m.delete())
    tokens = [x for x in re.findall(r"[A-Za-z0-9_\-]{20,}", raw)]
    if not tokens:
        return await bot.send_message(m.chat.id, "Не вижу ключ. Отправьте его ещё раз или /cancel.")
    existing = await db_keys()
    model = await get_model("admin")
    lines = []
    for tkn in tokens[:5]:
        if tkn in existing:
            lines.append(f"• {mask_key(tkn)} — уже добавлен")
            continue
        st, _, err = await gem_request(tkn, model, "", [{"role": "user", "parts": [{"text": "Скажи: ок"}]}], max_tokens=256)
        if st in (200, 429):
            existing.append(tkn)
            lines.append(f"• {mask_key(tkn)} — ✅ добавлен" + (" (лимит сейчас исчерпан)" if st == 429 else ""))
        else:
            lines.append(f"• {mask_key(tkn)} — ❌ не подошёл ({esc(st)}: {esc(err[:120])})")
    await set_setting("ai_keys", "\n".join(existing))
    await log(m.from_user.id, "ai_keys_changed", f"всего в боте: {len(existing)}")
    await state.clear()
    await bot.send_message(m.chat.id, "🔑 <b>Результат:</b>\n" + "\n".join(lines), reply_markup=AI_BACK)


@panel.callback_query(F.data == "p:ai_keys")
async def p_ai_keys(c: CallbackQuery):
    keys = await db_keys()
    env_n = len(await get_keys()) - len(keys)
    rows = [[btn(f"🗑 {mask_key(k)}", f"p:ai_kd:{i}")] for i, k in enumerate(keys)]
    note = f"\n\nКлючей из переменных окружения: {env_n} (удаляются только в настройках Render)." if env_n else ""
    await show(c, "🔑 <b>Ключи в боте</b>\nНажмите, чтобы удалить:" + ("" if keys else "\n\nПока пусто.") + note,
               kb(rows + [[btn("⬅️ К настройкам ИИ", "p:ai")]]))


@panel.callback_query(F.data.startswith("p:ai_kd:"))
async def p_ai_keydel(c: CallbackQuery):
    i = int(c.data.split(":")[2])
    keys = await db_keys()
    if 0 <= i < len(keys):
        keys.pop(i)
        await set_setting("ai_keys", "\n".join(keys))
        await log(c.from_user.id, "ai_key_deleted", f"осталось {len(keys)}")
    await p_ai_keys(c)


@panel.callback_query(F.data == "p:ai_ma")
async def p_ai_model(c: CallbackQuery, state: FSMContext):
    await state.set_state(AiCfg.model_a)
    cur = await get_model("admin")
    await c.message.answer(f"🧠 Текущая модель: <code>{esc(cur)}</code>\n\nОтправьте название новой модели, например "
                           "<code>gemini-2.5-flash</code> или <code>gemini-2.5-pro</code>. Список доступных — кнопка "
                           "«📋 Доступные модели». /cancel — отмена")
    await c.answer()
