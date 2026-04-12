#!/usr/bin/env python3
"""
Ally For Projects — Telegram-бот для расписания встреч и напоминаний.

Зависимости и запуск см. README.md.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    Defaults,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Константы и эталон команды
# ---------------------------------------------------------------------------

MSK = ZoneInfo("Europe/Moscow")

# Роли с правами на создание/изменение/удаление мероприятий (должны совпадать с колонкой role в team.csv).
ADMIN_ROLES = frozenset({"Руководитель проекта", "Администратор проекта"})

# Нижнее меню (ReplyKeyboard): подписи кнопок
MENU_BTN_COMMANDS = "Список команд"
MENU_BTN_SCHEDULE = "Расписание"

# Подсказка только в режиме редактирования мероприятия (/change_event)
CE_EDIT_HINT = "Отправьте новое значение или нажмите кнопку."


def main_menu_reply_markup() -> ReplyKeyboardMarkup:
    """Постоянное нижнее меню для авторизованных пользователей."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(MENU_BTN_COMMANDS), KeyboardButton(MENU_BTN_SCHEDULE)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Меню",
    )


def schedule_period_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline-кнопки выбора периода для /schedule."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("На сегодня", callback_data="sch_today"),
                InlineKeyboardButton("На завтра", callback_data="sch_tomorrow"),
            ],
            [InlineKeyboardButton("На неделю", callback_data="sch_week")],
        ]
    )


def list_events_filter_keyboard() -> InlineKeyboardMarkup:
    """Фильтры для /list_events (полный список для руководителя и администратора)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("На сегодня", callback_data="list_today"),
                InlineKeyboardButton("На неделю", callback_data="list_week"),
            ],
            [InlineKeyboardButton("Все предстоящие", callback_data="list_upcoming")],
        ]
    )


def build_commands_help_plain_text(is_admin: bool) -> str:
    """
    Список команд (plain text): в Telegram команды /... подсвечиваются и нажимаются.
    """
    text = (
        "Вот список команд, который поможет тебе эффективнее пользоваться Ally For Projects.\n\n"
        "Для всех участников:\n"
        "/commands — этот список\n"
        "/schedule — расписание (сегодня / завтра / неделя)\n"
        "/event — карточка мероприятия (с id, например /event 3)\n"
        "/team — участники проектной команды (ФИО, роль, почта)\n"
    )
    if is_admin:
        text += (
            "\nТолько руководитель и администратор проекта:\n"
            "/add_event — добавить мероприятие\n"
            "/change_event — изменить (например /change_event 3)\n"
            "/del_event — удалить (например /del_event 3)\n"
            "/list_events — список id и фильтры\n"
        )
    return text


# Состояния ConversationHandler для /add_event
(ADD_TITLE, ADD_DATETIME, ADD_LOCATION, ADD_MEMBERS) = range(4)
# Состояния для /change_event
(CE_TITLE, CE_DATETIME, CE_LOCATION, CE_MEMBERS) = range(10, 14)
# Авторизация по ФИО
(AUTH_FIO,) = range(100, 101)

REMINDER_JOB_PREFIX = "reminder_event_"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "bot.sqlite3"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DEFAULT_DB_PATH)))
DEFAULT_TEAM_CSV_PATH = BASE_DIR / "team.csv"
TEAM_CSV_PATH = Path(os.getenv("TEAM_CSV_PATH", str(DEFAULT_TEAM_CSV_PATH)))

_db_lock = threading.Lock()


def team_csv_path() -> Path:
    """Путь к CSV со списком команды (переменная окружения TEAM_CSV_PATH или team.csv рядом с ботом)."""
    return TEAM_CSV_PATH


def load_team_roster_from_csv() -> list[dict[str, str]]:
    """
    Читает team.csv с колонками full_name, role, email.
    Пустые строки пропускаются. При ошибке чтения или формата выбрасывает исключение.
    """
    path = team_csv_path()
    if not path.is_file():
        raise FileNotFoundError(f"Не найден файл команды: {path}")

    members: list[dict[str, str]] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("В файле нет строки заголовков (full_name, role, email).")
            for row_num, raw in enumerate(reader, start=2):
                if raw is None:
                    continue
                norm = {
                    (k or "").strip().lower(): (v or "").strip()
                    for k, v in raw.items()
                    if k is not None
                }
                full_name = norm.get("full_name", "")
                role = norm.get("role", "")
                email = norm.get("email", "")
                if not full_name and not role and not email:
                    continue
                if not full_name or not role or not email:
                    raise ValueError(
                        f"Строка {row_num}: заполните все поля full_name, role, email "
                        f"(ФИО={full_name!r}, роль={role!r}, почта={email!r})."
                    )
                members.append(
                    {"full_name": full_name, "role": role, "email": email}
                )
    except OSError as exc:
        raise RuntimeError(f"Не удалось прочитать {path}: {exc}") from exc

    if not members:
        raise ValueError(f"В {path} нет ни одной строки с данными участника.")
    return members


def try_load_team_roster() -> tuple[list[dict[str, str]], Optional[str]]:
    """
    Безопасная загрузка команды для обработчиков.
    Возвращает (список, None) или ([], сообщение об ошибке для пользователя).
    """
    try:
        return load_team_roster_from_csv(), None
    except FileNotFoundError as exc:
        logger.warning("team.csv: %s", exc)
        return [], "Файл команды (team.csv) не найден. Обратитесь к администратору бота."
    except ValueError as exc:
        logger.warning("team.csv: %s", exc)
        return [], f"Ошибка в файле команды: {exc}"
    except RuntimeError as exc:
        logger.warning("team.csv: %s", exc)
        return [], f"Не удалось загрузить список команды: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Неожиданная ошибка при чтении team.csv")
        return [], "Не удалось загрузить список команды. Попробуйте позже."


def roster_indices_matching_members(
    roster: list[dict[str, str]], members: list[dict[str, str]]
) -> set[int]:
    """Индексы строк roster, совпадающие с текущими участниками события (по ФИО и роли)."""
    selected: set[int] = set()
    for m in members:
        want_fn = normalize_person_name(m.get("full_name", ""))
        role_m = (m.get("role") or "").strip()
        for i, r in enumerate(roster):
            if normalize_person_name(r["full_name"]) != want_fn:
                continue
            if r["role"].strip() != role_m:
                continue
            selected.add(i)
            break
    return selected


def abbreviate_fio_ru(full_name: str) -> str:
    """
    Сокращённое ФИО для кнопок: «Фамилия И. О.» из полного «Фамилия Имя Отчество» (как в team.csv).
    Одно слово возвращается как есть; два — «Фамилия И.».
    """
    parts = " ".join(full_name.strip().split()).split()
    if not parts:
        return full_name.strip()
    if len(parts) == 1:
        return parts[0]
    surname = parts[0]
    first = parts[1]
    first_i = (first[0] + ".") if first else ""
    if len(parts) == 2:
        return f"{surname} {first_i}".strip()
    patron = parts[2]
    patron_i = (patron[0] + ".") if patron else ""
    return f"{surname} {first_i} {patron_i}".strip()


def _member_pick_button_caption(
    m: dict[str, str],
    mark: str,
    *,
    show_role_on_button: bool,
    abbreviate_fio: bool = False,
    max_len: int = 64,
) -> str:
    """Текст на кнопке выбора участника (данные из team.csv). Укладывается в лимит Telegram (~64 символа)."""
    name = (m.get("full_name") or "").strip()
    if abbreviate_fio:
        name = abbreviate_fio_ru(name)
    role = (m.get("role") or "").strip()
    if show_role_on_button and role:
        cap = f"{mark}{name} ({role})"
    else:
        cap = f"{mark}{name}"
    if len(cap) <= max_len:
        return cap
    return cap[: max_len - 1] + "…"


def member_pick_keyboard(
    prefix: str,
    roster: list[dict[str, str]],
    selected: set[int],
    *,
    show_manual_row: bool = True,
    show_role_on_button: bool = False,
    abbreviate_fio_on_button: bool = False,
) -> InlineKeyboardMarkup:
    """
    Кнопки выбора участников для шага /add_event или /change_event.
    prefix: 'addm' (addm_t_0, addm_done) или 'cem' (+ опционально cem_man при show_manual_row).
    Для /add_event ручной ввод отключён — show_manual_row=False.
    При show_role_on_button подпись: «ФИО (роль в проекте)».
    abbreviate_fio_on_button — на кнопке «Фамилия И. О.» вместо полного ФИО из CSV.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i, m in enumerate(roster):
        mark = "✓ " if i in selected else ""
        label = _member_pick_button_caption(
            m,
            mark,
            show_role_on_button=show_role_on_button,
            abbreviate_fio=abbreviate_fio_on_button,
        )
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{prefix}_t_{i}")]
        )
    rows.append(
        [InlineKeyboardButton("Готово — сохранить выбор", callback_data=f"{prefix}_done")]
    )
    if show_manual_row:
        rows.append(
            [InlineKeyboardButton("Ввести списком вручную", callback_data=f"{prefix}_man")]
        )
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Утилиты строк и дат
# ---------------------------------------------------------------------------


def normalize_person_name(name: str) -> str:
    """Нормализация ФИО для сравнения (пробелы, регистр, ё/е)."""
    s = " ".join(name.strip().split())
    return s.casefold().replace("ё", "е")


def find_team_member(user_input: str) -> Optional[dict[str, str]]:
    """Ищет участника команды по введённому ФИО (данные из team.csv)."""
    want = normalize_person_name(user_input)
    roster, _ = try_load_team_roster()
    for row in roster:
        if normalize_person_name(row["full_name"]) == want:
            return dict(row)
    return None


def parse_event_datetime_msk(text: str) -> datetime:
    """
    Парсит одну строку: ДД-ММ-ГГГГ ЧЧ:ММ (время по Москве).
    Примеры: 15-04-2026 14:30
    """
    s = text.strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=MSK)
        except ValueError:
            continue
    raise ValueError(
        "Неверный формат. Укажите дату и время одной строкой: ДД-ММ-ГГГГ ЧЧ:ММ "
        "(например, 15-04-2026 14:30). Время — по Москве."
    )


def format_dt_msk(dt: datetime) -> str:
    """Человекочитаемый вывод даты/времени в МСК."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    dt = dt.astimezone(MSK)
    return dt.strftime("%d.%m.%Y %H:%M МСК")


def iso_msk(dt: datetime) -> str:
    """Сохранение в БД: ISO с офсетом Москвы."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK).isoformat(timespec="minutes")


def parse_iso_to_msk(iso_str: str) -> datetime:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def parse_member_lines(block: str) -> list[dict[str, str]]:
    """
    Участники построчно. В строке: «ФИО — Роль» или «ФИО - Роль» (длинное или короткое тире).
    """
    members: list[dict[str, str]] = []
    for raw_line in block.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        full_name, role = None, None
        for sep in (" — ", " – ", " - "):
            if sep in line:
                a, b = line.split(sep, 1)
                full_name, role = a.strip(), b.strip()
                break
        if not full_name or not role:
            raise ValueError(
                f"Строка не распознана: «{line}». Используйте формат: ФИО — Роль "
                "(например, Иванов Дмитрий Сергеевич — Бизнес-аналитик)."
            )
        members.append({"full_name": full_name, "role": role})
    if not members:
        raise ValueError("Список участников пуст. Отправьте хотя бы одну строку.")
    return members


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    with _db_lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_datetime TEXT NOT NULL,
                    location TEXT NOT NULL,
                    members_json TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def db_run(fn: Callable[[sqlite3.Connection], Any]) -> Any:
    with _db_lock:
        conn = _connect()
        try:
            return fn(conn)
        finally:
            conn.close()


def save_user(user_id: int, full_name: str, role: str) -> None:
    def op(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO users (user_id, full_name, role) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, role=excluded.role",
            (user_id, full_name, role),
        )
        conn.commit()

    db_run(op)


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    def op(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
        row = conn.execute(
            "SELECT user_id, full_name, role FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    return db_run(op)


def insert_event(title: str, event_datetime_iso: str, location: str, members: list[dict]) -> int:
    payload = json.dumps(members, ensure_ascii=False)

    def op(conn: sqlite3.Connection) -> int:
        cur = conn.execute(
            "INSERT INTO events (title, event_datetime, location, members_json) VALUES (?, ?, ?, ?)",
            (title, event_datetime_iso, location, payload),
        )
        conn.commit()
        return int(cur.lastrowid)

    return db_run(op)


def update_event_full(
    event_id: int,
    title: str,
    event_datetime_iso: str,
    location: str,
    members: list[dict],
) -> bool:
    payload = json.dumps(members, ensure_ascii=False)

    def op(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "UPDATE events SET title=?, event_datetime=?, location=?, members_json=? WHERE event_id=?",
            (title, event_datetime_iso, location, payload, event_id),
        )
        conn.commit()
        return cur.rowcount > 0

    return db_run(op)


def delete_event(event_id: int) -> bool:
    def op(conn: sqlite3.Connection) -> bool:
        cur = conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        conn.commit()
        return cur.rowcount > 0

    return db_run(op)


def get_event(event_id: int) -> Optional[dict[str, Any]]:
    def op(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
        row = conn.execute(
            "SELECT event_id, title, event_datetime, location, members_json FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["members"] = json.loads(d.pop("members_json"))
        return d

    return db_run(op)


def list_events_all() -> list[dict[str, Any]]:
    def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT event_id, title, event_datetime, location, members_json FROM events ORDER BY event_datetime"
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["members"] = json.loads(d.pop("members_json"))
            out.append(d)
        return out

    return db_run(op)


def list_user_ids_by_full_name(full_name: str) -> list[int]:
    want = normalize_person_name(full_name)

    def op(conn: sqlite3.Connection) -> list[int]:
        rows = conn.execute("SELECT user_id, full_name FROM users").fetchall()
        return [int(r["user_id"]) for r in rows if normalize_person_name(r["full_name"]) == want]

    return db_run(op)


# ---------------------------------------------------------------------------
# Напоминания (JobQueue)
# ---------------------------------------------------------------------------


def reminder_job_name(event_id: int) -> str:
    return f"{REMINDER_JOB_PREFIX}{event_id}"


def cancel_reminder(application: Application, event_id: int) -> None:
    name = reminder_job_name(event_id)
    for job in application.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def send_event_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправка напоминания за 15 минут до начала."""
    event_id = context.job.data
    try:
        ev = get_event(int(event_id))
        if not ev:
            return
        dt = parse_iso_to_msk(ev["event_datetime"])
        loc_esc = html.escape(ev["location"])
        lines = [
            "Напоминание: через 15 минут начнётся мероприятие.",
            "",
            f"<b>{format_event_title_with_id_html(ev)}</b>",
            html.escape(format_dt_msk(dt)),
            loc_esc,
        ]
        text = "\n".join(lines)
        members = ev["members"]
        sent = 0
        for m in members:
            fn = m.get("full_name", "")
            uids = list_user_ids_by_full_name(fn)
            for uid in uids:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
                    sent += 1
                except Exception as exc:  # noqa: BLE001 — логируем и продолжаем
                    logger.warning("Не удалось отправить напоминание user_id=%s: %s", uid, exc)
        if sent == 0:
            logger.info("Напоминание по событию %s: нет совпадений ФИО с авторизованными.", event_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка в send_event_reminder: %s", exc)


def schedule_reminder(application: Application, event_id: int) -> None:
    """
    Планирует напоминание за 15 минут до начала (МСК).
    Старые задачи с тем же именем удаляются.
    """
    cancel_reminder(application, event_id)
    ev = get_event(event_id)
    if not ev:
        return
    start = parse_iso_to_msk(ev["event_datetime"])
    remind_at = start - timedelta(minutes=15)
    now = datetime.now(MSK)
    if remind_at <= now:
        logger.info("Событие %s: время напоминания уже прошло, задача не ставится.", event_id)
        return
    delay = remind_at - now
    application.job_queue.run_once(
        send_event_reminder,
        when=delay,
        data=event_id,
        name=reminder_job_name(event_id),
    )
    logger.info("Запланировано напоминание для события %s через %s", event_id, delay)


async def restore_all_reminders(application: Application) -> None:
    """После перезапуска — восстановить отложенные напоминания из БД."""
    events = list_events_all()
    now = datetime.now(MSK)
    for ev in events:
        try:
            start = parse_iso_to_msk(ev["event_datetime"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Пропуск события %s из-за неверной даты: %s", ev.get("event_id"), exc)
            continue
        if start <= now:
            continue
        schedule_reminder(application, int(ev["event_id"]))


# ---------------------------------------------------------------------------
# Проверки доступа
# ---------------------------------------------------------------------------


def is_registered(user_id: int) -> bool:
    return get_user(user_id) is not None


def is_admin_user(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u["role"] in ADMIN_ROLES)


async def reply_need_auth(update: Update) -> None:
    msg = "Сначала пройдите авторизацию через /start (кнопка «Авторизоваться»)."
    if update.message:
        await update.message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg)


async def reply_need_admin(update: Update) -> None:
    msg = "Эта команда доступна только ролям «Руководитель проекта» и «Администратор проекта»."
    if update.message:
        await update.message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg)


# ---------------------------------------------------------------------------
# Форматирование расписания
# ---------------------------------------------------------------------------


def msk_day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """Начало и конец календарного дня в МСК для переданного момента (в МСК)."""
    d = day.astimezone(MSK).date()
    start = datetime(d.year, d.month, d.day, tzinfo=MSK)
    end = start + timedelta(days=1)
    return start, end


def filter_events_period(events: list[dict], start: datetime, end: datetime) -> list[dict]:
    out = []
    for ev in events:
        try:
            dt = parse_iso_to_msk(ev["event_datetime"])
        except Exception:  # noqa: BLE001
            continue
        if start <= dt < end:
            out.append(ev)
    out.sort(key=lambda e: parse_iso_to_msk(e["event_datetime"]))
    return out


def user_listed_in_event_members(user_full_name: str, ev: dict) -> bool:
    """Совпадение ФИО пользователя со списком участников события."""
    want = normalize_person_name(user_full_name)
    for m in ev.get("members") or []:
        if normalize_person_name(m.get("full_name", "")) == want:
            return True
    return False


def filter_events_schedule_for_user(events: list[dict], user_full_name: str) -> list[dict]:
    """Для /schedule: только мероприятия, где пользователь указан участником."""
    return [e for e in events if user_listed_in_event_members(user_full_name, e)]


def format_event_title_with_id_html(ev: dict) -> str:
    """Название с id в квадратных скобках; текст названия экранируется для HTML."""
    return f"[{ev['event_id']}] {html.escape(ev['title'])}"


def format_event_card(ev: dict) -> str:
    """Карточка события в HTML (безопасно для произвольного текста)."""
    dt = parse_iso_to_msk(ev["event_datetime"])
    members = ev["members"]
    lines = [
        f"<b>Название:</b> {format_event_title_with_id_html(ev)}",
        "",
        f"<b>Дата и время:</b> {html.escape(format_dt_msk(dt))}",
        "",
        f"<b>Место:</b> {html.escape(ev['location'])}",
        "",
        "<b>Участники:</b>",
    ]
    for m in members:
        lines.append(
            f"• {html.escape(m['full_name'])} — {html.escape(m['role'])}"
        )
    return "\n".join(lines)


CARD_LIST_SEPARATOR = "\n\n" + ("─" * 14) + "\n\n"


def format_events_as_event_cards_html(events: list[dict]) -> str:
    """Несколько мероприятий в том же HTML-виде, что карточка /event <id>."""
    return CARD_LIST_SEPARATOR.join(format_event_card(ev) for ev in events)


# ---------------------------------------------------------------------------
# Обработчики: старт и авторизация
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я помогаю оперативно информировать команду о ключевых событиях и встречах проекта."
    )
    uid = update.effective_user.id
    if is_registered(uid):
        u = get_user(uid)
        fn = html.escape(u["full_name"])
        rl = html.escape(u["role"])
        await update.message.reply_text(
            f"{text}\n\nВы уже авторизованы как <b>{fn}</b> ({rl}). Время везде по Москве.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_reply_markup(),
        )
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Авторизоваться", callback_data="auth_start")]]
    )
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )


async def auth_callback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data != "auth_start":
        return ConversationHandler.END
    uid = update.effective_user.id
    if is_registered(uid):
        u = get_user(uid)
        await q.message.reply_text(
            f"Вы уже авторизованы: {u['full_name']} ({u['role']}).",
            reply_markup=main_menu_reply_markup(),
        )
        return ConversationHandler.END
    await q.message.reply_text(
        "Введите ваше **ФИО** так же, как в списке команды (например, Иванов Иван Иванович).",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AUTH_FIO


async def auth_receive_fio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    member = find_team_member(text)
    if not member:
        await update.message.reply_text(
            "ФИО не найдено в списке команды. Проверьте написание или обратитесь к администратору проекта. "
            "Попробуйте снова или отправьте /cancel."
        )
        return AUTH_FIO
    save_user(update.effective_user.id, member["full_name"], member["role"])
    await update.message.reply_text(
        f"Авторизация успешна.\n<b>ФИО:</b> {html.escape(member['full_name'])}\n"
        f"<b>Роль:</b> {html.escape(member['role'])}\n\n"
        "Ознакомьтесь со списком команд: нажмите «Список команд» внизу или отправьте <code>/commands</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_reply_markup(),
    )
    return ConversationHandler.END


async def auth_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Авторизация отменена. Нажмите /start, когда будете готовы.")
    return ConversationHandler.END


async def menu_open_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка нижнего меню «Список команд»."""
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        raise ApplicationHandlerStop()
    await cmd_commands(update, context)
    # Не отдавать то же сообщение диалогам (/add_event и т.д.)
    raise ApplicationHandlerStop()


async def menu_open_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка нижнего меню «Расписание»."""
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        raise ApplicationHandlerStop()
    await cmd_schedule(update, context)
    raise ApplicationHandlerStop()


# ---------------------------------------------------------------------------
# Команды для авторизованных
# ---------------------------------------------------------------------------


async def cmd_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    uid = update.effective_user.id
    is_adm = is_admin_user(uid)
    text = build_commands_help_plain_text(is_adm)
    await update.message.reply_text(text)


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    await update.message.reply_text(
        "Выберите период:",
        reply_markup=schedule_period_inline_keyboard(),
    )


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список ФИО с кнопками; по нажатию — карточка с почтой (данные из team.csv)."""
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    roster, err = try_load_team_roster()
    if err:
        await update.message.reply_text(err)
        return
    if not roster:
        await update.message.reply_text("Список команды пуст.")
        return
    rows = [
        [InlineKeyboardButton(m["full_name"][:64], callback_data=f"team_i_{i}")]
        for i, m in enumerate(roster)
    ]
    await update.message.reply_text(
        "Участники проектной команды\n"
        "(нажмите на участника, чтобы узнать о нем больше информации)",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def team_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ на кнопку с ФИО в /team."""
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if not is_registered(q.from_user.id):
        await reply_need_auth(update)
        return
    m = re.match(r"^team_i_(\d+)$", q.data or "")
    if not m:
        return
    idx = int(m.group(1))
    roster, err = try_load_team_roster()
    if err:
        await q.message.reply_text(err)
        return
    if idx < 0 or idx >= len(roster):
        await q.message.reply_text("Запись не найдена. Откройте /team снова.")
        return
    p = roster[idx]
    text = (
        f"<b>ФИО:</b> {html.escape(p['full_name'])}\n"
        f"<b>Роль в проекте:</b> {html.escape(p['role'])}\n"
        f"<b>Почта:</b> {html.escape(p['email'])}"
    )
    await q.message.reply_text(text, parse_mode=ParseMode.HTML)


async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    if not is_registered(uid):
        await reply_need_auth(update)
        return
    now = datetime.now(MSK)
    events = list_events_all()
    user_row = get_user(uid)
    my_name = user_row["full_name"] if user_row else ""
    if q.data == "sch_today":
        start, end = msk_day_bounds(now)
        in_period = filter_events_period(events, start, end)
        header = "На сегодня запланированы следующие мероприятия:"
        empty_period = "На сегодня мероприятий в календаре нет."
        empty_mine = "На сегодня нет мероприятий, где вы указаны участником."
    elif q.data == "sch_tomorrow":
        start, end = msk_day_bounds(now + timedelta(days=1))
        in_period = filter_events_period(events, start, end)
        header = "На завтра запланированы следующие мероприятия:"
        empty_period = "На завтра мероприятий в календаре нет."
        empty_mine = "На завтра нет мероприятий, где вы указаны участником."
    elif q.data == "sch_week":
        start, _ = msk_day_bounds(now)
        end = start + timedelta(days=7)
        in_period = filter_events_period(events, start, end)
        header = "На неделю запланированы следующие мероприятия:"
        empty_period = "На эту неделю в календаре мероприятий нет."
        empty_mine = "На эту неделю нет мероприятий, где вы указаны участником."
    else:
        return
    if not in_period:
        await q.message.reply_text(empty_period)
        return
    filtered = filter_events_schedule_for_user(in_period, my_name)
    if not filtered:
        await q.message.reply_text(empty_mine)
        return
    body = format_events_as_event_cards_html(filtered)
    full = f"{html.escape(header)}\n\n{body}"
    await q.message.reply_text(full, parse_mode=ParseMode.HTML)


async def cmd_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    if not context.args:
        await update.message.reply_text("Укажите id: /event <id>")
        return
    try:
        eid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Некорректный id. Пример: /event 3")
        return
    ev = get_event(eid)
    if not ev:
        await update.message.reply_text("Мероприятие с таким id не найдено.")
        return
    await update.message.reply_text(
        format_event_card(ev),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_reply_markup(),
    )


# ---------------------------------------------------------------------------
# /add_event (диалог)
# ---------------------------------------------------------------------------


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    em = update.effective_message
    if not em:
        return ConversationHandler.END
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return ConversationHandler.END
    if not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return ConversationHandler.END
    context.user_data["add_event"] = {}
    await em.reply_text("Шаг 1/4. Введите **название** мероприятия.", parse_mode=ParseMode.MARKDOWN)
    return ADD_TITLE


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["add_event"]["title"] = (update.message.text or "").strip()
    await update.message.reply_text(
        "Шаг 2/4. Введите **дату и время** одной строкой: `ДД-ММ-ГГГГ ЧЧ:ММ` (время по Москве). "
        "Пример: `15-04-2026 14:30`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADD_DATETIME


async def add_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dt = parse_event_datetime_msk(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return ADD_DATETIME
    context.user_data["add_event"]["event_datetime"] = dt
    await update.message.reply_text("Шаг 3/4. Укажите место проведения.")
    return ADD_LOCATION


async def add_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["add_event"]["location"] = (update.message.text or "").strip()
    draft = context.user_data["add_event"]
    roster, err = try_load_team_roster()
    if err or not roster:
        prefix = f"{err}\n\n" if err else ""
        await update.message.reply_text(
            f"{prefix}Шаг 4/4. Перечислите **участников** одним сообщением, **каждый с новой строки**:\n"
            "`ФИО — Роль в проекте`\n"
            "Разделитель между ФИО и ролью — только длинное (—) или короткое (-) тире.",
            parse_mode=ParseMode.MARKDOWN,
        )
        draft.pop("member_pick_selected", None)
        draft.pop("add_event_pick_only", None)
        return ADD_MEMBERS
    draft["member_pick_selected"] = set()
    draft["add_event_pick_only"] = True
    await update.message.reply_text(
        "Шаг 4/4. Добавьте участников мероприятия\n"
        "(отметьте участников в приложенном ниже списке)",
        reply_markup=member_pick_keyboard(
            "addm",
            roster,
            set(),
            show_manual_row=False,
            show_role_on_button=True,
            abbreviate_fio_on_button=True,
        ),
    )
    return ADD_MEMBERS


async def add_member_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inline-выбор участников на шаге 4 /add_event."""
    q = update.callback_query
    data = q.data or ""
    roster, err = try_load_team_roster()
    if err or not roster:
        await q.answer(
            "Список команды недоступен. Нажмите /cancel и повторите /add_event позже.",
            show_alert=True,
        )
        return ADD_MEMBERS

    draft = context.user_data.get("add_event")
    if not draft:
        await q.answer("Сессия сброшена. Начните снова с /add_event.", show_alert=True)
        return ConversationHandler.END

    selected: set[int] = draft.setdefault("member_pick_selected", set())

    if data == "addm_done":
        if not selected:
            await q.answer("Выберите хотя бы одного участника в списке ниже.", show_alert=True)
            return ADD_MEMBERS
        members = [
            {"full_name": roster[i]["full_name"], "role": roster[i]["role"]}
            for i in sorted(selected)
        ]
        title = draft.get("title", "")
        dt: datetime = draft["event_datetime"]
        location = draft.get("location", "")
        try:
            eid = insert_event(title, iso_msk(dt), location, members)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка сохранения мероприятия: %s", exc)
            await q.answer("Не удалось сохранить в базе. Попробуйте позже.", show_alert=True)
            return ADD_MEMBERS
        context.user_data.pop("add_event", None)
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Снять клавиатуру после сохранения: %s", exc)
        await q.message.reply_text(
            f"Мероприятие сохранено. <b>event_id:</b> <code>{eid}</code>\n"
            "Напоминание за 15 минут включено по умолчанию.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_reply_markup(),
        )
        schedule_reminder(context.application, eid)
        return ConversationHandler.END

    if data.startswith("addm_t_"):
        try:
            idx = int(data.rsplit("_", 1)[-1])
        except ValueError:
            await q.answer()
            return ADD_MEMBERS
        if idx < 0 or idx >= len(roster):
            await q.answer("Некорректный выбор.")
            return ADD_MEMBERS
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        await q.answer()
        try:
            await q.edit_message_reply_markup(
                reply_markup=member_pick_keyboard(
                    "addm",
                    roster,
                    set(selected),
                    show_manual_row=False,
                    show_role_on_button=True,
                    abbreviate_fio_on_button=True,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось обновить клавиатуру выбора участников: %s", exc)
        return ADD_MEMBERS

    await q.answer()
    return ADD_MEMBERS


async def add_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get("add_event", {})
    if draft.get("add_event_pick_only"):
        await update.message.reply_text(
            "Участников нужно выбрать кнопками под сообщением со списком. "
            "Текстом список на этом шаге не вводится."
        )
        return ADD_MEMBERS
    try:
        members = parse_member_lines(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return ADD_MEMBERS
    title = draft.get("title", "")
    dt: datetime = draft["event_datetime"]
    location = draft.get("location", "")
    eid = insert_event(title, iso_msk(dt), location, members)
    context.user_data.pop("add_event", None)
    await update.message.reply_text(
        f"Мероприятие сохранено. <b>event_id:</b> <code>{eid}</code>\n"
        "Напоминание за 15 минут включено по умолчанию.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_reply_markup(),
    )
    schedule_reminder(context.application, eid)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("add_event", None)
    extra: dict = {}
    if is_registered(update.effective_user.id):
        extra["reply_markup"] = main_menu_reply_markup()
    await update.message.reply_text("Создание мероприятия отменено.", **extra)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /change_event (диалог с пропуском шагов)
# ---------------------------------------------------------------------------


def ce_skip_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Оставить без изменений", callback_data=f"ce_skip_{field}")]]
    )


async def ce_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return ConversationHandler.END
    if not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text("Формат: /change_event <id> (пример: /change_event 3)")
        return ConversationHandler.END
    try:
        eid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Некорректный id.")
        return ConversationHandler.END
    ev = get_event(eid)
    if not ev:
        await update.message.reply_text("Мероприятие не найдено.")
        return ConversationHandler.END
    context.user_data["ce"] = {
        "event_id": eid,
        "title": ev["title"],
        "event_datetime": parse_iso_to_msk(ev["event_datetime"]),
        "location": ev["location"],
        "members": ev["members"],
    }
    await update.message.reply_text(
        f"<b>Название:</b> {format_event_title_with_id_html(ev)}\n\n{CE_EDIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ce_skip_kb("title"),
    )
    return CE_TITLE


async def ce_title_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["ce"]["title"] = (update.message.text or "").strip()
    await ask_ce_datetime(update, context)
    return CE_DATETIME


async def ce_skip_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Название без изменений.")
    await ask_ce_datetime_from_query(q, context)
    return CE_DATETIME


async def ask_ce_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    dt_s = html.escape(format_dt_msk(ce["event_datetime"]))
    await update.message.reply_text(
        f"<b>Дата и время:</b> {dt_s}\n\n"
        "Отправьте новое значение в формате <code>ДД-ММ-ГГГГ ЧЧ:ММ</code> или нажмите кнопку.",
        parse_mode=ParseMode.HTML,
        reply_markup=ce_skip_kb("dt"),
    )


async def ask_ce_datetime_from_query(q, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    dt_s = html.escape(format_dt_msk(ce["event_datetime"]))
    await q.message.reply_text(
        f"<b>Дата и время:</b> {dt_s}\n\n"
        "Отправьте новое значение в формате <code>ДД-ММ-ГГГГ ЧЧ:ММ</code> или нажмите кнопку.",
        parse_mode=ParseMode.HTML,
        reply_markup=ce_skip_kb("dt"),
    )


async def ce_datetime_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dt = parse_event_datetime_msk(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return CE_DATETIME
    context.user_data["ce"]["event_datetime"] = dt
    await ask_ce_location(update, context)
    return CE_LOCATION


async def ce_skip_dt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Дата и время без изменений.")
    await ask_ce_location_from_query(q, context)
    return CE_LOCATION


async def ask_ce_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    await update.message.reply_text(
        f"<b>Место проведения:</b> {html.escape(ce['location'])}\n\n{CE_EDIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ce_skip_kb("loc"),
    )


async def ask_ce_location_from_query(q, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    await q.message.reply_text(
        f"<b>Место проведения:</b> {html.escape(ce['location'])}\n\n{CE_EDIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ce_skip_kb("loc"),
    )


async def ce_location_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["ce"]["location"] = (update.message.text or "").strip()
    await ask_ce_members(update, context)
    return CE_MEMBERS


async def ce_skip_loc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Место без изменений.")
    await ask_ce_members_from_query(q, context)
    return CE_MEMBERS


async def ask_ce_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    roster, err = try_load_team_roster()
    if err or not roster:
        block = "\n".join(
            f"{html.escape(m['full_name'])} — {html.escape(m['role'])}" for m in ce["members"]
        )
        extra = f"{html.escape(err)}\n\n" if err else ""
        await update.message.reply_text(
            f"{extra}<b>Текущие участники</b> (введите новый список по одной строке):\n{block}\n\n{CE_EDIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=ce_skip_kb("mem"),
        )
        ce.pop("member_pick_selected", None)
        return
    ce["member_pick_selected"] = roster_indices_matching_members(roster, ce["members"])
    kb = member_pick_keyboard(
        "cem",
        roster,
        set(ce["member_pick_selected"]),
        show_role_on_button=True,
        abbreviate_fio_on_button=True,
    )
    kb.inline_keyboard.append(
        [InlineKeyboardButton("Оставить участников без изменений", callback_data="ce_skip_mem")]
    )
    await update.message.reply_text(
        "<b>Участники.</b> Отметьте состав кнопками (сокращённое ФИО и роль на кнопке) и нажмите «Готово», "
        "либо введите список текстом / нажмите «Ввести списком вручную».\n\n"
        f"{CE_EDIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def ask_ce_members_from_query(q, context: ContextTypes.DEFAULT_TYPE) -> None:
    ce = context.user_data["ce"]
    roster, err = try_load_team_roster()
    if err or not roster:
        block = "\n".join(
            f"{html.escape(m['full_name'])} — {html.escape(m['role'])}" for m in ce["members"]
        )
        extra = f"{html.escape(err)}\n\n" if err else ""
        await q.message.reply_text(
            f"{extra}<b>Текущие участники</b> (введите новый список по одной строке):\n{block}\n\n{CE_EDIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=ce_skip_kb("mem"),
        )
        ce.pop("member_pick_selected", None)
        return
    ce["member_pick_selected"] = roster_indices_matching_members(roster, ce["members"])
    kb = member_pick_keyboard(
        "cem",
        roster,
        set(ce["member_pick_selected"]),
        show_role_on_button=True,
        abbreviate_fio_on_button=True,
    )
    kb.inline_keyboard.append(
        [InlineKeyboardButton("Оставить участников без изменений", callback_data="ce_skip_mem")]
    )
    await q.message.reply_text(
        "<b>Участники.</b> Отметьте состав кнопками (сокращённое ФИО и роль на кнопке) и нажмите «Готово», "
        "либо введите список текстом / «Ввести списком вручную».\n\n"
        f"{CE_EDIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def ce_member_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inline-выбор участников в /change_event."""
    q = update.callback_query
    data = q.data or ""
    roster, err = try_load_team_roster()
    if err or not roster:
        await q.answer("Список команды недоступен. Введите участников текстом.", show_alert=True)
        return CE_MEMBERS

    ce = context.user_data.get("ce")
    if not ce:
        await q.answer("Сессия изменения сброшена. Начните с /change_event.", show_alert=True)
        return ConversationHandler.END

    selected: set[int] = ce.setdefault("member_pick_selected", set())

    if data == "cem_done":
        if not selected:
            await q.answer(
                "Выберите хотя бы одного участника или воспользуйтесь вводом вручную.",
                show_alert=True,
            )
            return CE_MEMBERS
        members = [
            {"full_name": roster[i]["full_name"], "role": roster[i]["role"]}
            for i in sorted(selected)
        ]
        eid = int(ce["event_id"])
        try:
            ok = update_event_full(
                eid,
                ce["title"],
                iso_msk(ce["event_datetime"]),
                ce["location"],
                members,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка обновления мероприятия %s: %s", eid, exc)
            await q.answer("Не удалось обновить запись.", show_alert=True)
            return CE_MEMBERS
        context.user_data.pop("ce", None)
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Снять клавиатуру после обновления: %s", exc)
        if ok:
            schedule_reminder(context.application, eid)
            await q.message.reply_text(
                f"Мероприятие <code>{eid}</code> обновлено.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_reply_markup(),
            )
        else:
            await q.message.reply_text("Не удалось обновить запись.")
        return ConversationHandler.END

    if data == "cem_man":
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Снять клавиатуру (ручной ввод): %s", exc)
        await q.message.reply_text(
            "Отправьте новый список участников одним сообщением, каждого с новой строки:\n"
            "<code>ФИО — Роль в проекте</code>",
            parse_mode=ParseMode.HTML,
        )
        return CE_MEMBERS

    if data.startswith("cem_t_"):
        try:
            idx = int(data.rsplit("_", 1)[-1])
        except ValueError:
            await q.answer()
            return CE_MEMBERS
        if idx < 0 or idx >= len(roster):
            await q.answer("Некорректный выбор.")
            return CE_MEMBERS
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        await q.answer()
        kb = member_pick_keyboard(
            "cem",
            roster,
            set(selected),
            show_role_on_button=True,
            abbreviate_fio_on_button=True,
        )
        kb.inline_keyboard.append(
            [InlineKeyboardButton("Оставить участников без изменений", callback_data="ce_skip_mem")]
        )
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось обновить клавиатуру выбора участников: %s", exc)
        return CE_MEMBERS

    await q.answer()
    return CE_MEMBERS


async def ce_members_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        members = parse_member_lines(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return CE_MEMBERS
    ce = context.user_data["ce"]
    eid = int(ce["event_id"])
    ok = update_event_full(
        eid,
        ce["title"],
        iso_msk(ce["event_datetime"]),
        ce["location"],
        members,
    )
    context.user_data.pop("ce", None)
    if ok:
        schedule_reminder(context.application, eid)
        await update.message.reply_text(
            f"Мероприятие <code>{eid}</code> обновлено.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_reply_markup(),
        )
    else:
        await update.message.reply_text("Не удалось обновить запись.")
    return ConversationHandler.END


async def ce_skip_mem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ce = context.user_data["ce"]
    eid = int(ce["event_id"])
    ok = update_event_full(
        eid,
        ce["title"],
        iso_msk(ce["event_datetime"]),
        ce["location"],
        ce["members"],
    )
    context.user_data.pop("ce", None)
    if ok:
        schedule_reminder(context.application, eid)
        await q.message.reply_text(
            f"Мероприятие <code>{eid}</code> обновлено (участники без изменений).",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_reply_markup(),
        )
    else:
        await q.message.reply_text("Не удалось обновить запись.")
    return ConversationHandler.END


async def ce_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("ce", None)
    extra: dict = {}
    if is_registered(update.effective_user.id):
        extra["reply_markup"] = main_menu_reply_markup()
    await update.message.reply_text("Изменение отменено.", **extra)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /del_event
# ---------------------------------------------------------------------------


async def cmd_del_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    if not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return
    if not context.args:
        await update.message.reply_text("Формат: /del_event <id> (пример: /del_event 3)")
        return
    try:
        eid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Некорректный id.")
        return
    ev = get_event(eid)
    if not ev:
        await update.message.reply_text("Мероприятие не найдено.")
        return
    dt = parse_iso_to_msk(ev["event_datetime"])
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да, удалить", callback_data=f"del_yes_{eid}"),
                InlineKeyboardButton("Отмена", callback_data="del_no"),
            ]
        ]
    )
    await update.message.reply_text(
        "Вы уверены, что хотите удалить это мероприятие?\n\n"
        f"<b>{format_event_title_with_id_html(ev)}</b> — {html.escape(format_dt_msk(dt))}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def del_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not is_registered(update.effective_user.id) or not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return
    if q.data == "del_no":
        await q.message.reply_text("Удаление отменено.")
        return
    m = re.match(r"^del_yes_(\d+)$", q.data or "")
    if not m:
        return
    eid = int(m.group(1))
    cancel_reminder(context.application, eid)
    if delete_event(eid):
        await q.message.reply_text(
            f"Мероприятие <code>{eid}</code> удалено.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await q.message.reply_text("Запись не найдена (возможно, уже удалена).")


# ---------------------------------------------------------------------------
# /list_events
# ---------------------------------------------------------------------------


async def cmd_list_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_user.id):
        await reply_need_auth(update)
        return
    if not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return
    await update.message.reply_text(
        "Выберите фильтр списка:",
        reply_markup=list_events_filter_keyboard(),
    )


def build_list_html(header: str, events: list[dict]) -> str:
    """Список для /list_events: заголовок + карточки как в /event (HTML)."""
    return f"{html.escape(header)}\n\n{format_events_as_event_cards_html(events)}"


async def list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not is_registered(update.effective_user.id) or not is_admin_user(update.effective_user.id):
        await reply_need_admin(update)
        return
    now = datetime.now(MSK)
    events = list_events_all()
    if q.data == "list_today":
        start, end = msk_day_bounds(now)
        filtered = filter_events_period(events, start, end)
        header = "Мероприятия на сегодня:"
        empty_msg = "В выбранном периоде мероприятий нет."
    elif q.data == "list_week":
        start, _ = msk_day_bounds(now)
        end = start + timedelta(days=7)
        filtered = filter_events_period(events, start, end)
        header = "Мероприятия на неделю:"
        empty_msg = "В выбранном периоде мероприятий нет."
    elif q.data == "list_upcoming":
        filtered = []
        for ev in events:
            try:
                dt = parse_iso_to_msk(ev["event_datetime"])
            except Exception:  # noqa: BLE001
                continue
            if dt > now:
                filtered.append(ev)
        filtered.sort(key=lambda e: parse_iso_to_msk(e["event_datetime"]))
        header = "Предстоящие мероприятия:"
        empty_msg = "Предстоящих мероприятий нет."
    else:
        return
    if not filtered:
        await q.message.reply_text(empty_msg)
        return
    text = build_list_html(header, filtered)
    await q.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Защита команд от неавторизованных (группа 1)
# ---------------------------------------------------------------------------


async def fallback_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Срабатывает для команд, для которых нет отдельного обработчика выше.
    Должен быть зарегистрирован последним в группе 0.
    """
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    if is_registered(uid):
        await update.message.reply_text("Команда не распознана. Откройте /commands.")
    else:
        await update.message.reply_text(
            "До авторизации доступен только /start. Нажмите «Авторизоваться» и введите ФИО."
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN в окружении или .env")

    defaults = Defaults(tzinfo=MSK)
    application = (
        Application.builder()
        .token(token)
        .defaults(defaults)
        .post_init(post_init_app)
        .build()
    )

    # Первыми: кнопки нижнего меню не должны уходить в ConversationHandler как обычный текст
    menu_filter = filters.TEXT & ~filters.COMMAND
    application.add_handler(
        MessageHandler(menu_filter & filters.Regex(f"^{re.escape(MENU_BTN_COMMANDS)}$"), menu_open_commands)
    )
    application.add_handler(
        MessageHandler(menu_filter & filters.Regex(f"^{re.escape(MENU_BTN_SCHEDULE)}$"), menu_open_schedule)
    )

    auth_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_callback_entry, pattern="^auth_start$")],
        states={
            AUTH_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_receive_fio)],
        },
        fallbacks=[CommandHandler("cancel", auth_cancel)],
        name="auth",
        persistent=False,
    )

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add_event", add_start)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_datetime)],
            ADD_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_location)],
            ADD_MEMBERS: [
                CallbackQueryHandler(
                    add_member_pick_callback, pattern=re.compile(r"^addm_(t_\d+|done)$")
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_members),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        name="add_event",
        persistent=False,
    )

    change_conv = ConversationHandler(
        entry_points=[CommandHandler("change_event", ce_start)],
        states={
            CE_TITLE: [
                CallbackQueryHandler(ce_skip_title, pattern="^ce_skip_title$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ce_title_msg),
            ],
            CE_DATETIME: [
                CallbackQueryHandler(ce_skip_dt, pattern="^ce_skip_dt$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ce_datetime_msg),
            ],
            CE_LOCATION: [
                CallbackQueryHandler(ce_skip_loc, pattern="^ce_skip_loc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ce_location_msg),
            ],
            CE_MEMBERS: [
                CallbackQueryHandler(
                    ce_member_pick_callback, pattern=re.compile(r"^cem_(t_\d+|done|man)$")
                ),
                CallbackQueryHandler(ce_skip_mem, pattern="^ce_skip_mem$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ce_members_msg),
            ],
        },
        fallbacks=[CommandHandler("cancel", ce_cancel)],
        name="change_event",
        persistent=False,
    )

    application.add_handler(auth_conv)
    application.add_handler(add_conv)
    application.add_handler(change_conv)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("commands", cmd_commands))
    application.add_handler(CommandHandler("schedule", cmd_schedule))
    application.add_handler(CommandHandler("team", cmd_team))
    application.add_handler(CommandHandler("event", cmd_event))
    application.add_handler(CommandHandler("del_event", cmd_del_event))
    application.add_handler(CommandHandler("list_events", cmd_list_events))

    application.add_handler(CallbackQueryHandler(schedule_callback, pattern="^sch_(today|tomorrow|week)$"))
    application.add_handler(
        CallbackQueryHandler(del_callback, pattern=re.compile(r"^del_(yes_\d+|no)$"))
    )
    application.add_handler(CallbackQueryHandler(list_callback, pattern="^list_(today|week|upcoming)$"))
    application.add_handler(CallbackQueryHandler(team_detail_callback, pattern=re.compile(r"^team_i_\d+$")))

    # Неизвестные команды (после всех CommandHandler / ConversationHandler)
    application.add_handler(MessageHandler(filters.COMMAND, fallback_unknown_command))

    return application


async def post_init_app(application: Application) -> None:
    """Инициализация после старта приложения."""
    init_db()
    try:
        team = load_team_roster_from_csv()
        logger.info("Список команды загружен из %s (%d чел.).", team_csv_path(), len(team))
    except Exception as exc:
        logger.error("Ошибка чтения team.csv: %s", exc)
        raise RuntimeError(
            "Не удалось загрузить team.csv. Проверьте файл и переменную TEAM_CSV_PATH (см. README)."
        ) from exc
    await restore_all_reminders(application)
    logger.info("Бот запущен, БД: %s", DB_PATH)


def main() -> None:
    init_db()
    app = build_application()
    logger.info("Запуск polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Критическая ошибка: %s", exc)
        raise
