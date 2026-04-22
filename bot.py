# bot.py — Telegram-бот для ставок SETTERS Coins
# Запуск локально: python3 bot.py
# На Railway запускается автоматически.

import os
import io
import csv
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ============ НАСТРОЙКИ ============
# Сначала пробуем прочитать из переменных окружения (так делает Railway),
# если их нет — берём значения из кода (для локального запуска).
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))

START_BALANCE = 20
# На Railway можно подключить volume и указать путь, например "/data/bot_data.db"
DB_FILE = os.environ.get("DB_FILE", "bot_data.db")
# ===================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Одно состояние диалога: ждём «Имя Фамилия» одним сообщением
WAITING_NAME = 1

TEAMS = [
    "Команда Кати Матвеевой",
    "Команда Ярослава Колесникова",
    "Команда Вероники Долгих",
    "Команда Насти Курбанаевой",
]

BET_TYPES = [
    "Команда - чемпион (I место)",
    "Победа в 5 из 10 испытаниях",
    "Капитан - победитель",
    "Ни 1 проигрыша из 10",
]

STAKE_AMOUNTS = [5, 10, 20]

# Файлы с картинками команд — порядок строго соответствует списку TEAMS
TEAM_IMAGES = [
    "team1.png",  # Команда Кати Матвеевой
    "team2.png",  # Команда Ярослава Колесникова
    "team3.png",  # Команда Вероники Долгих
    "team4.png",  # Команда Насти Курбанаевой
]

END_MESSAGE = "🏁 До встречи на Весёлых стартах 25 апреля!"


# ============ БАЗА ДАННЫХ ============

def init_db():
    """Создаёт таблицы, если их ещё нет."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name  TEXT,
            balance    INTEGER
        )
    """)
    # UNIQUE не даст одному юзеру поставить дважды на ту же комбинацию
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            team_idx      INTEGER,
            bet_type_idx  INTEGER,
            amount        INTEGER,
            created_at    TEXT,
            UNIQUE(user_id, team_idx, bet_type_idx)
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, first_name, last_name, balance FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def create_user(user_id, first_name, last_name):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO users (user_id, first_name, last_name, balance) "
        "VALUES (?,?,?,?)",
        (user_id, first_name, last_name, START_BALANCE),
    )
    conn.commit()
    conn.close()


def update_balance(user_id, new_balance):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
    conn.commit()
    conn.close()


def has_bet(user_id, team_idx, bet_type_idx):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM bets WHERE user_id=? AND team_idx=? AND bet_type_idx=?",
        (user_id, team_idx, bet_type_idx),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def add_bet(user_id, team_idx, bet_type_idx, amount):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO bets (user_id, team_idx, bet_type_idx, amount, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, team_idx, bet_type_idx, amount,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_all_users_with_bets():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, first_name, last_name, balance FROM users "
        "ORDER BY first_name, last_name"
    )
    users = cur.fetchall()
    result = []
    for u in users:
        cur.execute(
            "SELECT team_idx, bet_type_idx, amount, created_at FROM bets "
            "WHERE user_id=? ORDER BY created_at",
            (u[0],),
        )
        result.append({"user": u, "bets": cur.fetchall()})
    conn.close()
    return result


# ============ КЛАВИАТУРЫ ============

def welcome_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚀 Начать", callback_data="begin")]]
    )


def teams_keyboard():
    buttons = [
        [InlineKeyboardButton(team, callback_data=f"team:{i}")]
        for i, team in enumerate(TEAMS)
    ]
    return InlineKeyboardMarkup(buttons)


def bet_types_keyboard(team_idx):
    buttons = [
        [InlineKeyboardButton(bt, callback_data=f"bettype:{team_idx}:{i}")]
        for i, bt in enumerate(BET_TYPES)
    ]
    buttons.append([InlineKeyboardButton("◀️ Назад к командам", callback_data="back_to_teams")])
    return InlineKeyboardMarkup(buttons)


def amounts_keyboard(team_idx, bet_type_idx):
    row = [
        InlineKeyboardButton(f"{a} монет",
                             callback_data=f"amount:{team_idx}:{bet_type_idx}:{a}")
        for a in STAKE_AMOUNTS
    ]
    buttons = [row, [InlineKeyboardButton("◀️ Назад", callback_data=f"team:{team_idx}")]]
    return InlineKeyboardMarkup(buttons)


def back_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Вернуться назад", callback_data="back_to_teams")]]
    )


# ============ ХЭНДЛЕРЫ ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start."""
    user = get_user(update.effective_user.id)
    if user is None:
        # Новый пользователь — показываем приветствие и кнопку «Начать»
        await update.message.reply_text(
            "Привет! Это агрегатор ставок на Весёлые старты ((SETTERS)). Готов начать?",
            reply_markup=welcome_keyboard(),
        )
    else:
        # Уже зарегистрирован — сразу показываем команды
        await show_teams_menu(update, balance=user[3])


async def begin_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка «Начать» — просим имя и фамилию одним сообщением."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Отлично! Напиши своё Имя и Фамилию в одном сообщении.\n\n"
        "Например: Иван Петров"
    )
    return WAITING_NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем «Имя Фамилия» и регистрируем пользователя."""
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "Пожалуйста, напиши и имя, и фамилию через пробел.\n"
            "Например: Иван Петров"
        )
        return WAITING_NAME

    first_name, last_name = parts[0], parts[1]
    create_user(update.effective_user.id, first_name, last_name)

    await update.message.reply_text(
        f"🎉 Регистрация завершена, {first_name}!\n"
        f"Тебе начислено {START_BALANCE} SETTERS Coins.\n\n"
        f"Коротко о механике:\n"
        f"Весёлые старты – это легендарное соревнование спортсменов, "
        f"которое будет состоять из 10 этапов.\n"
        f"Предлагаем подогреть азарт и проверить свою интуицию!\n\n"
        f"У тебя есть только 20 ((SETTERS)COINS). "
        f"Распределяй их по 5, 10 или ставь сразу все 20 на одно событие.\n\n"
        f"Какие есть ставки:\n"
        f"– Команда получит титул Чемпиона и I место\n"
        f"– Команда одержит победу в 5/10 испытаниях\n"
        f"– Капитан команды победит в конкурсе капитанов\n"
        f"– Команда не допустит ни одного проигрыша в 10/10 испытаниях\n\n"
        f"Выбирай команду для ставки:"
    )
    await show_teams_menu(update, balance=START_BALANCE)
    return ConversationHandler.END


async def show_teams_menu(update: Update, balance: int):
    """Показывает альбом из 4 картинок команд + текст с кнопками выбора."""
    # Определяем, куда слать сообщения (chat_id и bot объект)
    if update.message:
        chat_id = update.message.chat_id
        bot = update.message.get_bot()
    else:
        chat_id = update.callback_query.message.chat_id
        bot = update.callback_query.message.get_bot()

    # Пытаемся собрать альбом из 4 картинок
    media = []
    for idx, img_path in enumerate(TEAM_IMAGES):
        if os.path.exists(img_path):
            # Подпись добавляем только к первой картинке — она отображается под альбомом
            caption = TEAMS[idx] if idx == 0 else None
            media.append(InputMediaPhoto(media=open(img_path, "rb"), caption=caption))

    # Если удалось собрать хотя бы 2 картинки — отправляем альбомом (так красивее)
    if len(media) >= 2:
        await bot.send_media_group(chat_id=chat_id, media=media)

    # После альбома — текст с кнопками команд
    text = f"💰 Твой баланс: {balance} SETTERS Coins\n\nВыбери команду:"
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=teams_keyboard())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок (кроме «Начать» — её ловит ConversationHandler)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    user = get_user(user_id)
    if user is None:
        await query.message.reply_text("Сначала отправь /start для регистрации.")
        return

    balance = user[3]

    # Выбрана команда → показываем типы ставок
    if data.startswith("team:"):
        team_idx = int(data.split(":")[1])
        await query.edit_message_text(
            f"📋 {TEAMS[team_idx]}\n\n"
            f"💰 Баланс: {balance} монет\n\n"
            f"Выбери тип ставки:",
            reply_markup=bet_types_keyboard(team_idx),
        )

    # Выбран тип ставки → показываем суммы
    elif data.startswith("bettype:"):
        _, t_idx, b_idx = data.split(":")
        team_idx, bet_type_idx = int(t_idx), int(b_idx)

        if has_bet(user_id, team_idx, bet_type_idx):
            await query.answer("Ты уже ставил на эту комбинацию!", show_alert=True)
            return

        await query.edit_message_text(
            f"📋 {TEAMS[team_idx]}\n"
            f"🎯 Ставка: {BET_TYPES[bet_type_idx]}\n\n"
            f"💰 Баланс: {balance} монет\n\n"
            f"Выбери сумму ставки:",
            reply_markup=amounts_keyboard(team_idx, bet_type_idx),
        )

    # Выбрана сумма → делаем ставку
    elif data.startswith("amount:"):
        _, t_idx, b_idx, amt = data.split(":")
        team_idx, bet_type_idx, amount = int(t_idx), int(b_idx), int(amt)

        if has_bet(user_id, team_idx, bet_type_idx):
            await query.edit_message_text(
                "❌ Ты уже ставил на эту комбинацию.",
                reply_markup=back_keyboard(),
            )
            return

        if amount > balance:
            await query.edit_message_text(
                f"❌ Недостаточно монет. Баланс: {balance}, нужно: {amount}.",
                reply_markup=back_keyboard(),
            )
            return

        if not add_bet(user_id, team_idx, bet_type_idx, amount):
            await query.edit_message_text(
                "❌ Ставка уже сделана ранее.",
                reply_markup=back_keyboard(),
            )
            return

        new_balance = balance - amount
        update_balance(user_id, new_balance)

        confirm_text = (
            f"✅ Ставка принята!\n\n"
            f"📋 {TEAMS[team_idx]}\n"
            f"🎯 {BET_TYPES[bet_type_idx]}\n"
            f"💸 Сумма: {amount} монет\n\n"
            f"💰 Остаток: {new_balance} монет"
        )

        if new_balance == 0:
            # Баланс закончился — финальное сообщение
            confirm_text += f"\n\n{END_MESSAGE}"
            await query.edit_message_text(confirm_text)
        else:
            await query.edit_message_text(confirm_text, reply_markup=back_keyboard())

    # Назад к списку команд
    elif data == "back_to_teams":
        # Скрываем кнопки у предыдущего сообщения, чтобы оно не висело активным
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # И показываем свежее меню с альбомом команд
        await show_teams_menu(update, balance=balance)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin — текстовая сводка + CSV-файл со всеми ставками."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Команда доступна только администратору.")
        return

    data = get_all_users_with_bets()
    if not data:
        await update.message.reply_text("Пока никто не зарегистрировался.")
        return

    # Текстовая сводка
    lines = ["📊 СПИСОК УЧАСТНИКОВ И СТАВОК"]
    total_bets = 0
    for item in data:
        uid, fn, ln, bal = item["user"]
        lines.append(f"\n👤 {fn} {ln}")
        lines.append(f"   Баланс: {bal} монет")
        if not item["bets"]:
            lines.append("   — ставок нет")
        else:
            for b in item["bets"]:
                t_idx, bt_idx, amt, ts = b
                lines.append(f"   • {TEAMS[t_idx]} → {BET_TYPES[bt_idx]} — {amt} монет")
                total_bets += 1
    lines.append(f"\n— Всего участников: {len(data)}")
    lines.append(f"— Всего ставок: {total_bets}")

    full = "\n".join(lines)
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i + 4000])

    # CSV-файл (открывается в Excel/Numbers)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Имя", "Фамилия", "Telegram ID", "Баланс",
                     "Команда", "Тип ставки", "Сумма", "Время"])
    for item in data:
        uid, fn, ln, bal = item["user"]
        if not item["bets"]:
            writer.writerow([fn, ln, uid, bal, "", "", "", ""])
        else:
            for b in item["bets"]:
                t_idx, bt_idx, amt, ts = b
                writer.writerow([fn, ln, uid, bal,
                                 TEAMS[t_idx], BET_TYPES[bt_idx], amt, ts])

    # utf-8-sig — чтобы Excel на Mac корректно открыл кириллицу
    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"bets_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await update.message.reply_document(
        document=InputFile(io.BytesIO(csv_bytes), filename=filename),
        caption="📎 Таблица всех ставок (открой в Excel или Numbers)",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Регистрация отменена. Чтобы начать заново — отправь /start"
    )
    return ConversationHandler.END


# ============ ЗАПУСК ============

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # /start вне диалога — просто показывает приветствие или меню
    app.add_handler(CommandHandler("start", start))

    # Диалог регистрации: запускается нажатием кнопки «Начать»
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(begin_registration, pattern="^begin$")],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    app.add_handler(CommandHandler("admin", admin_cmd))
    # Все остальные кнопки (кроме «Начать», которую забрал ConversationHandler)
    app.add_handler(CallbackQueryHandler(on_button))

    logger.info("Бот запущен. Ctrl+C — остановить.")
    app.run_polling()


if __name__ == "__main__":
    main()
