# bot_part05.py — часть 5 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
PAGES.update({
    "u_userview": ("📱 Что видит пользователь", (
        "<b>📱 Что видит пользователь</b>\n\n"
        "<b>/start</b> — приветствие (текст и картинка задаются главным админом) и три кнопки:\n"
        "• <b>👥 Посмотреть админов</b> — теги и пол слушателей, у отдыхающих пометка «💤 отдыхает». Юзернеймов и ID нет.\n"
        "• <b>📜 Правила</b> — правила бота.\n"
        "• <b>🙋 Стать админом</b> — анкета: вступление → «▶️ Заполнить анкету» → вопросы по одному → проверка → "
        "«✅ Отправить» / «🔁 Заново» / «❌ Отмена». Отмена в любой момент — /cancel.\n\n"
        "<b>Команды пользователя:</b> /start, /stop (завершить диалог), /del (реплаем на своё сообщение — удалить его "
        "у слушателей), /cancel (отменить анкету).\n\n"
        "<b>Сообщения от бота:</b> подтверждение «сообщение передано» (при первом сообщении), приветствие взявшего "
        "админа, ответы админов (без плашки «переслано»), «слушатель отошёл» (если админ ушёл на отдых), текст о "
        "завершении диалога, сообщения о бане/автобане, решение по анкете. Все системные тексты меняются в "
        "/admin → 💬 Тексты.")),
    "u_ai": ("🤖 ИИ-помощник для админов", (
        "<b>🤖 ИИ-помощник для админов</b>\n\n"
        "Он знает методичку, все команды, кнопки и логику бота (на Gemini). Спросите, как что-то сделать — он ответит "
        "пошагово.\n\n"
        "• <b>/ai вопрос</b> — разовый ответ (в группе и в личке), например <code>/ai как вернуть пользователя в очередь</code>.\n"
        "• <b>/ai</b> без текста (в личке) — режим диалога: просто пишите вопросы. Выход — /exit или «⬅️ Выйти».\n"
        "• <b>/ai reset</b> или кнопка «🧹 Новый диалог» — сбросить память разговора.\n"
        "• «👨‍💻 Живой человек» — кнопка со ссылкой на человека из тех поддержки.\n\n"
        "Важно: ИИ не видит реальные диалоги пользователей. Он может ошибаться — если не знает ответа или вы не "
        "уверены, он сам направит к человеку. Количество вопросов в час ограничено. Главный админ может отключить "
        "ИИ для админов.")),
    "m_ai": ("🤖 Настройка ИИ (Gemini)", (
        "<b>🤖 Настройка ИИ</b> — /admin → «🤖 ИИ» (работает и из группы)\n\n"
        "ИИ-помощник доступен админам (команда /ai); главные админы получают расширенную базу знаний. "
        "Для пользователей ИИ не используется.\n\n"
        "• <b>🔑 Добавить ключ</b> — API-ключ Gemini (aistudio.google.com). Можно несколько. Бот проверяет ключ "
        "тестовым запросом и удаляет ваше сообщение с ключом (нужно право «Удалять сообщения»; в группе лучше "
        "делать это в личке).\n"
        "• <b>🗑 Ключи</b> — список с масками, нажатие удаляет. Ключи из переменной окружения удаляются только в Render.\n"
        "• <b>🧠 Модель</b> — название модели, например <code>gemini-2.5-flash</code>; "
        "бот проверяет, что модель существует.\n"
        "• <b>📋 Доступные модели</b> — какие модели доступны вашему ключу.\n"
        "• <b>🧪 Тест</b> — проверка модели и каждого ключа.\n"
        "• <b>🛡 ИИ для админов</b> — включить или выключить ИИ-помощника (/ai).\n"
        "• <b>📝 Доп. инструкции</b> — ваши указания ИИ («-» очищает).\n"
        "• <b>👨‍💻 Контакт человека</b> — куда ИИ направляет, если не знает ответа (по умолчанию @gavrilov_983).\n\n"
        "Ключи пробуются по очереди: если у одного закончился лимит, берётся следующий. Если не работают все — "
        "админ получает сообщение с контактом человека, причина пишется в логи (ai_key_failed, ai_error). "
        "Лимит вопросов в час: <code>AI_ADMIN_LIMIT</code> (100). "
        "Число запросов к ИИ видно в аналитике.")),
    "m_apply": ("📋 Анкета «Стать админом»", (
        "<b>📋 Анкета «Стать админом»</b>\n\n"
        "<b>Путь пользователя:</b> кнопка «🙋 Стать админом» → вступление → вопросы по одному → проверка → «✅ Отправить». "
        "Анкета приходит в привязанную группу карточкой «📝 Анкета #N» (имя, юзернейм, ID и ответы) с кнопками "
        "«✅ Принять» и «❌ Отклонить» — нажимать их могут <b>только главные админы</b>.\n\n"
        "• После решения пользователю уходит текст «принято» или «отклонено» (тексты задаёте вы), карточка помечается "
        "и показывает, кто решил.\n"
        "• После принятия появляется кнопка «👮 Добавить в админы» — запускает регистрацию (пол → тег → приветствие), "
        "ID и юзернейм подставляются сами; новый админ получает уведомление.\n"
        "• Одна анкета на рассмотрении на человека; после отказа повторно — через <code>APPLY_COOLDOWN_H</code> часов (72). "
        "Забаненные и действующие админы подать анкету не могут.\n\n"
        "<b>Панель /admin → «📋 Анкета»:</b>\n"
        "• список вопросов; «✏️ №N» — изменить, «🗑 №N» — удалить (минимум один, максимум 15);\n"
        "• «➕ Добавить вопрос», «♻️ По умолчанию» (вернуть стандартные);\n"
        "• «Приём анкет: вкл/выкл» — открыть или закрыть набор;\n"
        "• «✉️ Вступление», «✉️ Отправлена», «✅ Текст «принято»», «❌ Текст «отклонено»» — тексты анкеты;\n"
        "• вверху — статистика заявок: ждут / принято / отклонено.")),
})

PAGES["u_topics"] = ("🧵 Чат с темами", (
    "<b>🧵 Чат с темами (форум)</b>\n\n"
    "Если привязанная группа — форум, у каждого пользователя появляется <b>своя тема</b> с его ником. Тема создаётся при "
    "первом сообщении и переиспользуется, если человек вернётся.\n\n"
    "<b>Название темы показывает статус:</b> 🆕 ждёт слушателя · 💬 в работе (рядом тег админа) · 🔒 закрыта.\n\n"
    "<b>Как общаться:</b>\n"
    "• Просто написали в теме — сообщение ушло пользователю как есть.\n"
    "• Ответили (reply) на сообщение — пользователь получит ваш ответ именно на то своё/ваше сообщение. "
    "Когда пользователь отвечает на сообщение в личке, оно приходит в тему тоже как ответ.\n"
    "• «/» в начале — заметка коллегам, пользователю не уходит.\n"
    "• Правка своего сообщения меняет его у пользователя; правка пользователя меняет сообщение в теме.\n"
    "• Команды /close, /release, /info, /ban, /del работают прямо в теме, без reply. /del — реплаем на своё сообщение.\n\n"
    "<b>Взять пользователя:</b> кнопка в карточке внутри темы или в уведомлении в «Общей» теме "
    "(рядом кнопка «🔗 Открыть тему»). Метки «Ведёт» в темах нет — тема принадлежит диалогу.\n\n"
    "Главный админ: режим включается/выключается в /admin → Настройки; <code>/cleantopics [дней]</code> удаляет темы закрытых "
    "диалогов. Боту нужны права «Управлять темами», «Закреплять» и «Удалять сообщения».\n"
    "Если группа не форум — работает обычный режим с пересылкой."))
U_ORDER = ["u_flow", "u_userview", "u_dialog", "u_topics", "u_cmds", "u_ai", "u_rest", "u_norma", "u_safe"]
M_ORDER = ["m_start", "m_panel", "m_texts", "m_admins", "m_groups", "m_bc", "m_stats", "m_settings", "m_ai", "m_apply", "m_cmds"]


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


# ═════════════════ ИИ-ПОМОЩНИК (Gemini) ═════════════════
class AiS(StatesGroup):
    admin_chat = State()


class ApplyS(StatesGroup):
    q = State()
    confirm = State()


HUMAN_MARK = "[[HUMAN]]"
GEM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
AI_ADMIN_LIMIT = int(os.getenv("AI_ADMIN_LIMIT", "100"))  # вопросов к ИИ в час (админ)
APPLY_COOLDOWN_H = int(os.getenv("APPLY_COOLDOWN_H", "72"))
_ai_hist: dict = {}
_ai_rl: dict = {}
_http_session = None

DEFAULT_QUESTIONS = [
    "Сколько вам лет?",
    "Ваш пол?",
    "Какой тег вы бы хотели (например #луна)?",
    "Расскажите о себе: чем вы живёте, что вас вдохновляет?",
    "Почему вы хотите стать слушателем?",
    "Есть ли у вас опыт эмоциональной поддержки? Если да — расскажите.",
    "Сколько времени в неделю вы готовы уделять боту?",
    "Ваш часовой пояс и удобное время для дежурств?",
]


# ───────── Gemini: ключи, модели, запросы ─────────
async def human_contact():
    return await get_setting("human_contact", os.getenv("HUMAN_CONTACT", "@gavrilov_983"))


def human_url(contact):
    c = contact.strip()
    return c if c.startswith("http") else "https://t.me/" + c.lstrip("@")


def mask_key(k):
    return k[:6] + "…" + k[-4:] if len(k) > 12 else "***"


async def db_keys():
    raw = await get_setting("ai_keys", "")
    return [x.strip() for x in raw.split("\n") if x.strip()]


async def get_keys():
    keys = await db_keys()
    for x in re.split(r"[,\s]+", os.getenv("GEMINI_API_KEY", "")):
        if x and x not in keys:
            keys.append(x)
    return keys


async def get_model(kind):
    return await get_setting(f"ai_model_{kind}", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))


async def http():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    return _http_session


async def gem_request(key, model, system, contents, max_tokens=2048, temperature=0.4):
    """Один запрос к Gemini. Возвращает (http_status, text|None, error)."""
    body = {"contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        s = await http()
        async with s.post(GEM_URL.format(model=model), json=body,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                err = ""
                if isinstance(data, dict):
                    err = (data.get("error") or {}).get("message", "")
                return resp.status, None, (err or str(data))[:300]
    except Exception as e:  # noqa
        return 0, None, f"{type(e).__name__}: {e}"
    cand = {}
    try:
        cand = data["candidates"][0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
    except Exception:  # noqa
        text = ""
    if not text:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or cand.get("finishReason") or "?"
        return 200, None, f"пустой ответ ({reason})"
    return 200, text, ""


async def gem_ask(model, system, contents):
    keys = await get_keys()
    if not keys:
        return None, "нет API-ключей"
    last = "?"
    for i, key in enumerate(keys):
        st, text, err = await gem_request(key, model, system, contents)
        if text:
            return text, ""
        last = f"{st}: {err}"
        await log("system", "ai_key_failed", f"ключ #{i + 1} ({mask_key(key)}): {last}")
        if st == 404:
            break  # модель не существует — другие ключи не помогут
    return None, last


# ───────── База знаний ─────────
def strip_html(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t))


def kb_pages(keys):
    return "\n\n".join(f"### {PAGES[k][0]}\n{strip_html(PAGES[k][1])}" for k in keys)
