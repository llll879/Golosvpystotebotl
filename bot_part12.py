# bot_part12.py — часть 12 из 12 бота «Голос в пустоте». Вставляйте целиком, без правок.
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
    ai_n = [await cnt("SELECT COUNT(*) c FROM events WHERE kind IN ('ai_user','ai_admin') AND ts>=?", ago(d))
            for d in (0, 7, 30)]
    ai_all = await cnt("SELECT COUNT(*) c FROM events WHERE kind IN ('ai_user','ai_admin')")
    app_s = {x["status"]: x["c"] for x in await db.fetchall("SELECT status, COUNT(*) c FROM applications GROUP BY status")}

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
        "🤖 <b>ИИ и анкеты</b>\n"
        f"{row('Запросов к ИИ', ai_n)} · всего {ai_all}\n"
        f"Анкеты: ждут {app_s.get('pending', 0)} · принято {app_s.get('accepted', 0)} · "
        f"отклонено {app_s.get('rejected', 0)}\n\n"
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
                                                           "Кто возьмёт?", reply_parameters=rp,
                                            message_thread_id=await dthread(d["group_id"], d["user_id"])))
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
