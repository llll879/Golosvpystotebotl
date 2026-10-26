"""Голос в пустоте — запуск бота.
Код бота разбит на файлы bot_part01.py … bot_part12.py (лежат рядом с этим файлом).
Запуск на Render: python bot.py"""
import os
import sys

TOTAL = 12
BASE = os.path.dirname(os.path.abspath(__file__))

missing = [f"bot_part{i:02d}.py" for i in range(1, TOTAL + 1)
           if not os.path.exists(os.path.join(BASE, f"bot_part{i:02d}.py"))]
if missing:
    sys.exit("Не найдены файлы рядом с bot.py: " + ", ".join(missing) + ". Загрузите их в репозиторий.")

ns = {"__name__": "__main__", "__file__": os.path.join(BASE, "bot_main.py")}
for i in range(1, TOTAL + 1):
    path = os.path.join(BASE, f"bot_part{i:02d}.py")
    with open(path, encoding="utf-8") as fh:
        exec(compile(fh.read(), path, "exec"), ns)
