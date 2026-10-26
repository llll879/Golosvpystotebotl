# bot_part11.py — часть 11 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
async def _save_model(m: Message, state: FSMContext, kind):
    name = (m.text or "").strip().replace("models/", "")
    if not re.fullmatch(r"[A-Za-z0-9._\-]{3,80}", name):
        return await m.answer("Некорректное название. Пример: <code>gemini-2.5-flash</code>")
    keys = await get_keys()
    warn = ""
    if keys:
        st, _, err = await gem_request(keys[0], name, "", [{"role": "user", "parts": [{"text": "Скажи: ок"}]}], max_tokens=256)
        if st == 404:
            return await m.answer(f"❌ Модель не найдена ({esc(err[:150])}). Проверьте название или посмотрите список.")
        if st != 200:
            warn = f"\n⚠️ Тестовый запрос вернул {st}: {esc(err[:120])} — модель всё равно сохранена."
    await set_setting(f"ai_model_{kind}", name)
    await log(m.from_user.id, "ai_model_changed", f"{kind}={name}")
    await state.clear()
    await m.answer(f"✅ Модель для {'пользователей' if kind == 'user' else 'админов'}: <code>{esc(name)}</code>{warn}",
                   reply_markup=AI_BACK)


@panel.message(AiCfg.model_a, NOCMD)
async def p_ai_model_a(m: Message, state: FSMContext):
    await _save_model(m, state, "admin")


@panel.callback_query(F.data == "p:ai_models")
async def p_ai_models(c: CallbackQuery):
    keys = await get_keys()
    if not keys:
        return await c.answer("Сначала добавьте API-ключ", show_alert=True)
    try:
        s = await http()
        async with s.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                         headers={"x-goog-api-key": keys[0]}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                return await c.answer(f"Ошибка {resp.status}: {str((data.get('error') or {}).get('message', ''))[:150]}",
                                      show_alert=True)
    except Exception as e:  # noqa
        return await c.answer(f"Ошибка: {e}", show_alert=True)
    names = sorted(x["name"].replace("models/", "") for x in data.get("models", [])
                   if "generateContent" in x.get("supportedGenerationMethods", []) and "gemini" in x["name"])
    body = "\n".join(f"• <code>{esc(n)}</code>" for n in names[:50]) or "Ничего не найдено."
    await show(c, "📋 <b>Доступные модели</b> (для текущего ключа):\n\n" + body, AI_BACK)


@panel.callback_query(F.data == "p:ai_test")
async def p_ai_test(c: CallbackQuery):
    keys = await get_keys()
    if not keys:
        return await c.answer("Сначала добавьте API-ключ", show_alert=True)
    await c.answer("Проверяю…")
    lines = []
    for kind in ("admin",):
        model = await get_model(kind)
        t0 = time.monotonic()
        st, text, err = await gem_request(keys[0], model, "", [{"role": "user", "parts": [{"text": "Ответь одним словом: ок"}]}], max_tokens=256)
        dt = time.monotonic() - t0
        lines.append(f"{'✅' if st == 200 else '❌'} <code>{esc(model)}</code> — "
                     f"{dt:.1f} с" + ("" if st == 200 else f" · {st}: {esc(err[:120])}"))
    bad = []
    for i, k in enumerate(keys):
        st, _, err = await gem_request(k, await get_model("admin"), "", [{"role": "user", "parts": [{"text": "ок"}]}], max_tokens=64)
        bad.append(f"{'✅' if st in (200,) else '❌'} ключ #{i + 1} {mask_key(k)}" + ("" if st == 200 else f" — {st}"))
    await c.message.answer("🧪 <b>Тест</b>\n\n" + "\n".join(lines) + "\n\n<b>Ключи:</b>\n" + "\n".join(bad), reply_markup=AI_BACK)


@panel.callback_query(F.data == "p:ai_prompt")
async def p_ai_prompt(c: CallbackQuery, state: FSMContext):
    await state.set_state(AiCfg.prompt)
    cur = (await get_setting("ai_extra", "")).strip() or "—"
    await c.message.answer(f"📝 <b>Дополнительные инструкции для ИИ</b>\nОни добавляются к его базе знаний (например: "
                           f"«отвечай короче», «упоминай, что набор в команду открыт»).\n\nСейчас: {esc(cur)}\n\n"
                           "Отправьте новый текст или «-» чтобы очистить. /cancel — отмена")
    await c.answer()


@panel.message(AiCfg.prompt, NOCMD)
async def p_ai_prompt_save(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Нужен текст.")
    val = "" if m.text.strip() == "-" else m.text.strip()[:2000]
    await set_setting("ai_extra", val)
    await log(m.from_user.id, "ai_extra_changed")
    await state.clear()
    await m.answer("✅ Сохранено." if val else "✅ Очищено.", reply_markup=AI_BACK)


@panel.callback_query(F.data == "p:ai_contact")
async def p_ai_contact(c: CallbackQuery, state: FSMContext):
    await state.set_state(AiCfg.contact)
    await c.message.answer(f"👨‍💻 Сейчас ИИ отправляет к: {esc(await human_contact())}\n\nОтправьте новый контакт "
                           "(@юзернейм или ссылку t.me/…). /cancel — отмена")
    await c.answer()


@panel.message(AiCfg.contact, NOCMD)
async def p_ai_contact_save(m: Message, state: FSMContext):
    v = (m.text or "").strip()
    if not v or len(v) > 100 or " " in v:
        return await m.answer("Нужен @юзернейм или ссылка без пробелов.")
    if not v.startswith(("@", "http")):
        v = "@" + v
    await set_setting("human_contact", v)
    await log(m.from_user.id, "human_contact_changed", v)
    await state.clear()
    await m.answer(f"✅ Контакт: {esc(v)}", reply_markup=AI_BACK)


# ── Анкета: настройки ──
async def apply_menu():
    qs = await get_questions()
    st = {x["status"]: x["c"] for x in await db.fetchall("SELECT status, COUNT(*) c FROM applications GROUP BY status")}
    on = await flag("f_apply")
    text = ("📋 <b>Анкета «Стать админом»</b>\n\n"
            f"Приём анкет: {'открыт ✅' if on else 'закрыт ⛔'}\n"
            f"Заявок: ждут {st.get('pending', 0)} · принято {st.get('accepted', 0)} · отклонено {st.get('rejected', 0)}\n\n"
            "<b>Вопросы:</b>\n" + "\n".join(f"{i + 1}. {esc(q)}" for i, q in enumerate(qs)))
    rows = [[btn(f"✏️ №{i + 1}", f"p:aqe:{i}"), btn(f"🗑 №{i + 1}", f"p:aqd:{i}")] for i in range(len(qs))]
    rows += [[btn("➕ Добавить вопрос", "p:aq_add"), btn("♻️ По умолчанию", "p:aq_reset")],
             [btn(f"Приём анкет: {'вкл' if on else 'выкл'}", "p:apf")],
             [btn("✉️ Вступление", "p:t:apply_intro"), btn("✉️ Отправлена", "p:t:apply_sent")],
             [btn("✅ Текст «принято»", "p:t:apply_accepted"), btn("❌ Текст «отклонено»", "p:t:apply_rejected")],
             BACK]
    return text, kb(rows)


APPLY_BACK = kb([[btn("⬅️ К анкете", "p:apply")]])


@panel.callback_query(F.data == "p:apply")
async def p_apply(c: CallbackQuery, state: FSMContext):
    await state.clear()
    t, k = await apply_menu()
    await show(c, t, k)


@panel.callback_query(F.data == "p:apf")
async def p_apply_flag(c: CallbackQuery):
    new = "0" if await flag("f_apply") else "1"
    await set_setting("f_apply", new)
    await log(c.from_user.id, "setting_toggled", f"f_apply={new}")
    t, k = await apply_menu()
    await show(c, t, k)


@panel.callback_query(F.data == "p:aq_add")
async def p_aq_add(c: CallbackQuery, state: FSMContext):
    if len(await get_questions()) >= 15:
        return await c.answer("Максимум 15 вопросов", show_alert=True)
    await state.set_state(ApplyCfg.add)
    await c.message.answer("➕ Отправьте текст нового вопроса (до 300 символов). /cancel — отмена")
    await c.answer()


@panel.message(ApplyCfg.add, NOCMD)
async def p_aq_add_save(m: Message, state: FSMContext):
    q = " ".join((m.text or "").split())[:300]
    if not q:
        return await m.answer("Нужен текст вопроса.")
    qs = await get_questions()
    qs.append(q)
    await set_setting("apply_questions", "\n".join(qs))
    await log(m.from_user.id, "apply_question_added")
    await state.clear()
    await m.answer("✅ Вопрос добавлен.", reply_markup=APPLY_BACK)


@panel.callback_query(F.data.startswith("p:aqe:"))
async def p_aq_edit(c: CallbackQuery, state: FSMContext):
    i = int(c.data.split(":")[2])
    qs = await get_questions()
    if i >= len(qs):
        return await c.answer("Нет такого вопроса", show_alert=True)
    await state.set_state(ApplyCfg.edit)
    await state.update_data(i=i)
    await c.message.answer(f"✏️ Вопрос №{i + 1}: {esc(qs[i])}\n\nОтправьте новый текст. /cancel — отмена")
    await c.answer()


@panel.message(ApplyCfg.edit, NOCMD)
async def p_aq_edit_save(m: Message, state: FSMContext):
    q = " ".join((m.text or "").split())[:300]
    if not q:
        return await m.answer("Нужен текст вопроса.")
    i = (await state.get_data())["i"]
    qs = await get_questions()
    if i < len(qs):
        qs[i] = q
        await set_setting("apply_questions", "\n".join(qs))
        await log(m.from_user.id, "apply_question_edited", str(i + 1))
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=APPLY_BACK)


@panel.callback_query(F.data.startswith("p:aqd:"))
async def p_aq_del(c: CallbackQuery):
    i = int(c.data.split(":")[2])
    qs = await get_questions()
    if len(qs) <= 1:
        return await c.answer("Должен остаться хотя бы один вопрос", show_alert=True)
    if i < len(qs):
        qs.pop(i)
        await set_setting("apply_questions", "\n".join(qs))
        await log(c.from_user.id, "apply_question_deleted", str(i + 1))
    t, k = await apply_menu()
    await show(c, t, k)


@panel.callback_query(F.data == "p:aq_reset")
async def p_aq_reset(c: CallbackQuery):
    await del_setting("apply_questions")
    await log(c.from_user.id, "apply_questions_reset")
    t, k = await apply_menu()
    await show(c, t, k)


# ── Анкета: решение главных админов ──
async def _decide_app(c: CallbackQuery, status):
    aid = int(c.data.split(":")[1])
    app = await db.fetchone("UPDATE applications SET status=?, decided_at=? WHERE id=? AND status='pending' RETURNING *",
                            status, now(), aid)
    if not app:
        return await c.answer("Анкета уже рассмотрена", show_alert=True)
    adm = await get_admin(c.from_user)
    who = await admin_label(adm) if adm else esc(c.from_user.full_name)
    delivered = True
    try:
        await send_html(app["user_id"], await txt("apply_accepted" if status == "accepted" else "apply_rejected"))
    except Exception:  # noqa
        delivered = False
    pairs = json.loads(app["answers"])
    footer = (("✅ <b>Принята</b>" if status == "accepted" else "❌ <b>Отклонена</b>") + f" · решил {who}"
              + ("" if delivered else "\n⚠️ Пользователь недоступен (заблокировал бота) — сообщение не доставлено."))
    markup = kb([[btn("👮 Добавить в админы", f"apply_adm:{aid}")]]) if status == "accepted" else None
    await safe(c.message.edit_text(app_card_text(app, pairs, footer), reply_markup=markup))
    await log(c.from_user.id, f"apply_{status}", f"app {aid}")
    await c.answer("Готово")


@panel.callback_query(F.data.startswith("apply_ok:"))
async def apply_ok(c: CallbackQuery):
    await _decide_app(c, "accepted")


@panel.callback_query(F.data.startswith("apply_no:"))
async def apply_no(c: CallbackQuery):
    await _decide_app(c, "rejected")


@panel.callback_query(F.data.startswith("apply_adm:"))
async def apply_to_admin(c: CallbackQuery, state: FSMContext):
    aid = int(c.data.split(":")[1])
    app = await db.fetchone("SELECT * FROM applications WHERE id=?", aid)
    if not app:
        return await c.answer("Анкета не найдена", show_alert=True)
    if await db.fetchone("SELECT id FROM admins WHERE user_id=?", app["user_id"]):
        return await c.answer("Он уже в админах", show_alert=True)
    await state.clear()
    await state.set_state(AddAdm.gender)
    await state.update_data(level=1, user_id=app["user_id"], username=(app["username"] or "").lower() or None)
    await c.message.answer(f"➕ Добавляем в админы заявителя по анкете #{aid}.\n\n2/4 Пол:",
                           reply_markup=kb([[btn(v, f"p:ng:{k}") for k, v in GENDER.items()]]))
    await c.answer()


# ── Импорт старых пользователей (до подключения этого кода, например из Livegram) ──
class ImpS(StatesGroup):
    wait = State()
    confirm = State()
    legacy = State()


IMPORT_EPOCH = "2000-01-01 00:00:00"  # метка «импортирован»: такие пользователи не считаются новыми/активными


def _ids_from_json(obj, out):
    if isinstance(obj, list):
        for it in obj:
            _ids_from_json(it, out)
    elif isinstance(obj, dict):
        v = obj.get("user_id", obj.get("id"))
        if v is not None:
            _ids_from_json(v, out)
        else:
            for x in obj.values():
                if isinstance(x, (list, dict)):
                    _ids_from_json(x, out)
    elif isinstance(obj, (int, str)) and str(obj).strip().isdigit():
        out.append(int(str(obj).strip()))


def parse_ids(raw):
    """Достаёт Telegram-ID пользователей из текста, CSV, TXT или JSON (в таблице ID — в первом столбце)."""
    nums = []
    try:
        data = json.loads(raw)
        if isinstance(data, (list, dict)):
            _ids_from_json(data, nums)
    except Exception:  # noqa
        for line in raw.splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            if re.fullmatch(r"[\d\s,;]+", line):  # строка из одних чисел — берём все
                nums += [int(x) for x in re.findall(r"\d+", line)]
            else:  # CSV-строка — ID в первом столбце
                mm = re.search(r"(?<![\w.\-])\d{5,13}(?![\w.])", line)
                if mm:
                    nums.append(int(mm.group(0)))
    out, seen = [], set()
    for v in nums:
        if 10000 <= v < 10 ** 13 and v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def import_menu():
    tot = await cnt("SELECT COUNT(*) c FROM users")
    imp = await cnt("SELECT COUNT(*) c FROM users WHERE first_seen=?", IMPORT_EPOCH)
    back = await cnt("SELECT COUNT(*) c FROM users WHERE first_seen=? AND last_seen>?", IMPORT_EPOCH, IMPORT_EPOCH)
    alive = await cnt("SELECT COUNT(*) c FROM users WHERE alive=1 AND banned=0")
    legacy = int(await get_setting("legacy_users", "0") or 0)
    text = ("📥 <b>Импорт старых пользователей</b>\n\n"
            "Telegram не позволяет боту узнать, кто писал ему раньше, поэтому ID тех, кто пользовался ботом до "
            "подключения этого кода (например, в Livegram), нужно загрузить списком. После импорта они попадают в "
            "рассылку и в аналитику.\n\n"
            "<b>Как:</b> выгрузите ID из старого бота (или другого источника) и отправьте сюда файл .txt / .csv / .json "
            "либо просто текст с ID (по одному в строке или через запятую). В таблицах ID должен быть в первом столбце.\n\n"
            "⚠️ Рассылка дойдёт только до тех, кто когда-либо запускал <b>именно этого бота</b> (по этому токену) и не "
            "заблокировал его. Недоступные бот пометит сам при первой рассылке и дальше не будет трогать.\n\n"
            f"Сейчас в базе: <b>{tot}</b> · импортировано: {imp} (уже вернулись: {back}) · "
            f"доступны для рассылки: {alive}\n"
            + (f"Старых пользователей без ID (только для аналитики): {legacy}\n" if legacy else ""))
    return text, kb([[btn("📤 Загрузить список ID", "p:imp_go")],
                     [btn("🔢 Число старых без ID", "p:imp_legacy")], BACK])


@panel.callback_query(F.data == "p:imp")
async def p_imp(c: CallbackQuery, state: FSMContext):
    await state.clear()
    t, k = await import_menu()
    await show(c, t, k)


@panel.callback_query(F.data == "p:imp_go")
async def p_imp_go(c: CallbackQuery, state: FSMContext):
    await state.set_state(ImpS.wait)
    await c.message.answer("📤 Отправьте файл (.txt, .csv, .json) или текст со списком ID пользователей. /cancel — отмена")
    await c.answer()


@panel.message(ImpS.wait, NOCMD)
async def p_imp_got(m: Message, state: FSMContext):
    raw = ""
    if m.document:
        name = (m.document.file_name or "").lower()
        if name.endswith((".xlsx", ".xls", ".zip", ".rar", ".7z", ".pdf")):
            return await m.answer("Этот формат не читается. Сохраните список как .csv или .txt и отправьте снова.")
        if (m.document.file_size or 0) > 15 * 1024 * 1024:
            return await m.answer("Файл больше 15 МБ — разбейте его на части.")
        try:
            buf = io.BytesIO()
            await bot.download(m.document, destination=buf)
            raw = buf.getvalue().decode("utf-8", "ignore")
        except Exception as e:  # noqa
            return await m.answer(f"Не удалось скачать файл: {esc(e)}")
    elif m.text:
        raw = m.text
    ids = parse_ids(raw)
    if not ids:
        return await m.answer("Не нашёл ни одного ID. Нужны числовые Telegram-ID (по одному в строке или через запятую).")
    existing = {x["user_id"] for x in await db.fetchall("SELECT user_id FROM users")}
    new = [i for i in ids if i not in existing]
    await state.update_data(ids=new)
    await state.set_state(ImpS.confirm)
    sample = ", ".join(str(i) for i in ids[:3])
    await m.answer(f"🔎 Найдено уникальных ID: <b>{len(ids)}</b>\n• новых: <b>{len(new)}</b>\n• уже в базе: {len(ids) - len(new)}\n"
                   f"Примеры: <code>{sample}</code>\n\nСверьте примеры с вашим списком (это точно ID пользователей?) и подтвердите.",
                   reply_markup=kb([[btn(f"✅ Добавить {len(new)}", "p:imp_ok")], [btn("❌ Отмена", "p:imp")]]))


@panel.callback_query(ImpS.confirm, F.data == "p:imp_ok")
async def p_imp_ok(c: CallbackQuery, state: FSMContext):
    ids = (await state.get_data()).get("ids", [])
    await state.clear()
    for i in range(0, len(ids), 1000):
        await db.executemany(
            "INSERT INTO users(user_id,username,name,first_seen,last_seen,alive,banned) VALUES(?,?,?,?,?,1,0) "
            "ON CONFLICT(user_id) DO NOTHING",
            [(uid, None, "(импорт)", IMPORT_EPOCH, IMPORT_EPOCH) for uid in ids[i:i + 1000]])
    await log(c.from_user.id, "users_imported", f"{len(ids)} ID")
    t, k = await import_menu()
    await show(c, f"✅ Добавлено пользователей: <b>{len(ids)}</b>. Теперь они участвуют в рассылках и аналитике.\n\n" + t, k)


@panel.callback_query(F.data == "p:imp_legacy")
async def p_imp_legacy(c: CallbackQuery, state: FSMContext):
    await state.set_state(ImpS.legacy)
    cur = await get_setting("legacy_users", "0")
    await c.message.answer(f"🔢 Сколько пользователей было у старого бота, <b>ID которых нет</b>? (сейчас: {esc(cur)})\n"
                           "Число покажется в аналитике отдельной строкой, в рассылке эти люди участвовать не смогут. "
                           "0 — убрать. /cancel — отмена")
    await c.answer()


@panel.message(ImpS.legacy, NOCMD)
async def p_imp_legacy_save(m: Message, state: FSMContext):
    v = (m.text or "").strip().replace(" ", "")
    if not v.isdigit():
        return await m.answer("Нужно число, например 1500.")
    await set_setting("legacy_users", str(int(v)))
    await log(m.from_user.id, "legacy_users_set", v)
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("📥 К импорту", "p:imp")], BACK]))


# ── Настройки ──
SETTING_KEYS = ("f_label", "f_react", "f_remind", "f_antispam", "f_userdel", "f_reactions", "f_topics")


async def settings_kb():
    f = {k: await flag(k) for k in SETTING_KEYS}
    onoff = lambda v: "вкл" if v else "выкл"  # noqa
    return kb([
        [btn(f"Метка в группе: {'@юзернейм' if f['f_label'] else '#тег'}", "p:sf:f_label")],
        [btn(f"Реакция 👍 на ответы админов: {onoff(f['f_react'])}", "p:sf:f_react")],
        [btn(f"Напоминания о неразобранных: {onoff(f['f_remind'])}", "p:sf:f_remind")],
        [btn(f"Автобан за спам: {onoff(f['f_antispam'])}", "p:sf:f_antispam")],
        [btn(f"Удаление сообщений пользователем: {onoff(f['f_userdel'])}", "p:sf:f_userdel")],
        [btn(f"Передача реакций: {onoff(f['f_reactions'])}", "p:sf:f_reactions")],
        [btn(f"Режим тем (форум-чаты): {onoff(f['f_topics'])}", "p:sf:f_topics")], BACK])


SETTINGS_TEXT = ("🔧 <b>Настройки</b>\n\n"
                 "• <b>Метка в группе</b> — после сообщения пользователя бот пишет «Ведёт: @юзернейм» или тег. "
                 "Видно только в группе.\n"
                 "• <b>Реакция</b> — 👍 на ответ админа, когда он ушёл пользователю.\n"
                 "• <b>Напоминания</b> — если обращение не взяли за 10 минут, бот напомнит в группе.\n"
                 f"• <b>Автобан</b> — больше {SPAM_N} сообщений за {SPAM_SEC} с = блокировка.\n"
                 "• <b>Удаление</b> — разрешает пользователю /del на свои сообщения.\n"
                 "• <b>Реакции</b> — передача реакций между пользователем и админами (от имени бота).\n"
                 "• <b>Режим тем</b> — если привязанный чат — форум, у каждого пользователя своя тема.")


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
