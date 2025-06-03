import logging
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BotCommand, BotCommandScopeChat, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command  # ✅ ЭТА СТРОКА — обязательна!
from datetime import datetime
import os
import json
from collections import defaultdict, deque
from aiogram.dispatcher.middlewares.base import BaseMiddleware


# ==========================
# 🔹 Настройки бота и таблицы
# ==========================

TOKEN = "6974697621:AAHM4qa91k4nq4Hsbn-rSDTkL8-6hAsa3pA"  # Укажи свой токен прямо в коде или загрузи из переменной окружения
SHEET_ID = "1QaR920L5bZUGNLk02M-lgXr9c5_nHJQVoPgPL7UVVY4"
ADMIN_IDS = ["665932047", "473541446"]  # Telegram ID админа

# Загрузка JSON-ключей из переменной окружения
with open("/root/Tlcbot/credentials/tlcbot-453608-3ac701333130.json") as f:
    google_creds_json = json.load(f)


# Авторизация в Google Sheets через JSON-ключи
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(google_creds_json, scopes=scope)
gc = gspread.authorize(credentials)

# Подключение к Google Sheets
spreadsheet = gc.open_by_key(SHEET_ID)

# Получаем листы
users_sheet = spreadsheet.worksheet("Пользователи")
tracking_sheet = spreadsheet.worksheet("Трекинг")
china_sheet = spreadsheet.worksheet("Китай")
kz_sheet = spreadsheet.worksheet("Казахстан")
issued_sheet = spreadsheet.worksheet("Выданное")  # Лист для выданных посылок
texts_sheet = spreadsheet.worksheet("Тексты")  # Подключаем лист с текстами

TEXTS = {}  # Глобальный словарь для хранения текстов

def load_texts():
    global TEXTS
    records = texts_sheet.get_all_values()
    TEXTS = {row[0]: row[1] for row in records if len(row) > 1}  # Загружаем все тексты в память

def get_text(key, **kwargs):
    text = TEXTS.get(key, f"⚠️ Текст '{key}' не найден!")  # Берем текст из памяти
    return text.format(**kwargs)  # Подставляем значения, если есть



# ==========================
# 🔹 FSM для регистрации
# ==========================
class Registration(StatesGroup):
    name = State()
    city = State()
    phone = State()
    manager_code = State()

# 🔹 FSM для управления треками (Добавляем сюда!)
class TrackManagement(StatesGroup):
    selecting_track = State()
    adding_signature = State()
    deleting_track = State()



# ==========================
# 🔹 Создание клавиатуры для пользователей
# ==========================
user_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📦 Проверить статус посылок"), KeyboardButton(text="🖊 Подписать трек-номер")],
        [KeyboardButton(text="❌ Удалить трек-номер"), KeyboardButton(text="📞 Связаться с менеджером")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False  # Клавиатура остаётся на экране
)

class QueueMiddleware(BaseMiddleware):
    def __init__(self, max_queue: int = 10):
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.waiting_count: dict[int, int] = {}
        self.max_queue = max_queue

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        if user_id not in self.user_locks:
            self.user_locks[user_id] = asyncio.Lock()
            self.waiting_count[user_id] = 0

        lock = self.user_locks[user_id]

        if lock.locked():
            if self.waiting_count[user_id] >= self.max_queue:
                return  # Пропускаем сообщение
            self.waiting_count[user_id] += 1
            async with lock:
                self.waiting_count[user_id] -= 1
                return await handler(event, data)
        else:
            async with lock:
                return await handler(event, data)

        
# Создание бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
dp.message.middleware(QueueMiddleware())  # <-- эта строка остаётся здесь


# ==========================
# 🔹 Команды бота
USER_COMMANDS = [
    BotCommand(command="register", description="📝 Регистрация"),
    BotCommand(command="check_status", description="📦 Проверить статус посылок"),
    BotCommand(command="sign_track", description="🖊 Подписать трек-номер"),
    BotCommand(command="delete_track", description="❌ Удалить трек-номер"),
    BotCommand(command="contact_manager", description="📞 Связаться с менеджером"),
    BotCommand(command="cancel", description="❌ Отменить текущее действие"),
]

ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="check_china", description="🇨🇳 Проверить Китай"),
    BotCommand(command="check_kz", description="🇰🇿 Проверить Казахстан"),
    BotCommand(command="check_issued", description="📦 Обновить 'Выданное'"),
    BotCommand(command="push", description="📢 Массовая рассылка"),
    BotCommand(command="update_texts", description="🔄 Обновить тексты уведомлений"),
]

async def set_bot_commands():

    await bot.set_my_commands(USER_COMMANDS)

    for admin_id in ADMIN_IDS:

        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))

# ==========================
# 🔹 Обработчики команд
# ==========================

# ✅ /start
@router.message(F.text == "/start")
async def start_handler(message: Message):
    logging.info(f"/start от {message.from_user.id}")
    await message.answer(get_text("start_message"))

# Храним ID пользователей, у которых идёт обработка шага
processing_users = set()

@router.message(Command("register"))
async def register_command(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    # Проверка: уже зарегистрирован?
    existing = users_sheet.col_values(1)
    if user_id in existing:
        await message.answer(get_text("already_registered"))
        return

    # Защита от двойных кликов и лагов
    if user_id in processing_users:
        await message.answer("⏳ Подождите, идёт обработка предыдущего шага...")
        return

    processing_users.add(user_id)
    try:
        await state.clear()
        await state.update_data(user_id=user_id)
        await state.set_state(Registration.name)
        await asyncio.sleep(0.1)
        await message.answer(get_text("ask_name"))
    finally:
        processing_users.discard(user_id)

@router.message(Registration.name)
async def register_name_handler(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return

    await state.update_data(name=message.text.strip())
    await state.set_state(Registration.city)
    await message.answer(get_text("ask_city"))

@router.message(Registration.city)
async def register_city_handler(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return

    await state.update_data(city=message.text.strip())
    await state.set_state(Registration.phone)
    await message.answer(get_text("ask_phone"))

@router.message(Registration.phone)
async def register_phone_handler(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return

    await state.update_data(phone=message.text.strip())
    await state.set_state(Registration.manager_code)
    await message.answer(get_text("ask_manager_code"))

@router.message(Registration.manager_code)
async def register_manager_handler(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return

    data = await state.get_data()
    user_id = data["user_id"]
    name = data["name"]
    city = data["city"]
    phone = data["phone"]
    manager_code = message.text.strip()

    logging.info(f"✅ Регистрируем: {user_id}, {name}, {city}, {phone}, код: {manager_code}")

    users_sheet.append_row([user_id, name, city, phone, manager_code])
    await message.answer(get_text("registration_complete"), reply_markup=user_keyboard)
    await state.clear()


# ✅ /check_status – проверка треков (Оптимизированная версия)
@router.message(F.text.in_(["📦 Проверить статус посылок", "/check_status"]))
async def check_status_handler(message: Message):
    user_id = str(message.from_user.id)
    
    # Загружаем все данные
    tracking_records = tracking_sheet.get_all_values()
    china_records = {row[0].strip().lower(): row[2] for row in china_sheet.get_all_values()[1:] if len(row) > 2}  # Китай (трек -> дата)
    kz_records = {row[0].strip().lower(): row[2] for row in kz_sheet.get_all_values()[1:] if len(row) > 2}  # Казахстан (трек -> дата)
    issued_records = {row[0].strip().lower(): row[2] for row in issued_sheet.get_all_values()[1:] if len(row) > 2}  # Выданное (трек -> дата)

    user_tracks = []
    
    # Находим все треки пользователя
    for row in tracking_records[1:]:
        if len(row) > 4 and row[4] == user_id:  # Проверяем ID пользователя в 5-й колонке
            track_number = row[0].strip().lower()  # Трек-номер
            signature = row[3] if len(row) > 3 and row[3] else "Без подписи"  # Подпись

            # Определяем статус, дату и индикатор
            if track_number in issued_records:
                indicator, status = "✅", "Выдана"
                date = issued_records[track_number]
            elif track_number in kz_records:
                indicator, status = "🟢", "Прибыла в Казахстан"
                date = kz_records[track_number]
            elif track_number in china_records:
                indicator, status = "🔵", "В пути до Алматы"
                date = china_records[track_number]
            else:
                indicator, status = "🟠", "Ожидается на складе в Китае"
                date = ""

            # Добавляем трек в список
            user_tracks.append((indicator, status, track_number.upper(), date, signature))

    # Сортируем список: 🟠 Трекинг → 🔵 Китай → 🟢 Казахстан → ✅ Выданное
    user_tracks.sort(key=lambda x: ["🟠", "🔵", "🟢", "✅"].index(x[0]))

    # Формируем текст ответа
    if not user_tracks:
        await message.answer("📭 У вас нет активных трек-номеров.")
        return

    text = get_text("status_header") + "\n"
    for indicator, status, track_number, date, signature in user_tracks:
        date_part = f" ({date})" if date else ""
        text += f"{indicator} {status}: {track_number}{date_part} ({signature})\n"

    await message.answer(text)


# Определяем состояния FSM (Добавь в начало файла)
class TrackSigning(StatesGroup):
    selecting_track = State()
    entering_signature = State()



# Хранилище активных пользователей в процессе FSM
active_states = {}

# ✅ Подписать трек-номер
@router.message(F.text.in_(["🖊 Подписать трек-номер", "/sign_track"]))
async def sign_track_handler(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    # Предотвращаем повторный запуск FSM
    current_state = await state.get_state()
    if current_state is not None:
        await message.answer("⏳ Пожалуйста, завершите предыдущее действие или введите /отмена.")
        return

    await state.clear()

    # Получаем треки пользователя
    user_tracks = [
        row[0].strip().upper()
        for row in tracking_sheet.get_all_values()
        if len(row) > 4 and row[4] == user_id
    ]

    if not user_tracks:
        await message.answer("📭 У вас нет активных трек-номеров.")
        return

    # Создаем клавиатуру с треками
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=track)] for track in user_tracks],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await state.set_state(TrackSigning.selecting_track)
    await asyncio.sleep(0.1)
    await message.answer("✏️ Выберите трек-номер, который хотите подписать:", reply_markup=keyboard)


# Обработка выбора трека
@router.message(TrackSigning.selecting_track)
async def process_track_selection(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return
    
    selected_track = message.text.strip().upper()
    user_id = str(message.from_user.id)

    # Проверка, есть ли трек у пользователя
    user_tracks = [
        row[0].strip().upper()
        for row in tracking_sheet.get_all_values()
        if len(row) > 4 and row[4] == user_id
    ]

    if selected_track not in user_tracks:
        await message.answer("❌ Такой трек не найден. Пожалуйста, выберите один из списка.")
        return

    await state.update_data(selected_track=selected_track)
    await state.set_state(TrackSigning.entering_signature)
    await asyncio.sleep(0.1)
    await message.answer("✏️ Введите подпись для выбранного трек-номера:", reply_markup=ReplyKeyboardRemove())

# Обработка подписи
@router.message(TrackSigning.entering_signature)
async def process_signature(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return
    
    user_id = str(message.from_user.id)
    data = await state.get_data()
    selected_track = data.get("selected_track")
    signature = message.text.strip()

    if not selected_track:
        await message.answer("⚠️ Что-то пошло не так. Введите /отмена и начните заново.")
        await state.clear()
        return

    # Обновляем подпись в таблице
    records = tracking_sheet.get_all_values()
    for i, row in enumerate(records):
        if row[0].strip().upper() == selected_track and len(row) > 4 and row[4] == user_id:
            tracking_sheet.update_cell(i + 1, 4, signature)  # Столбец D — подпись
            await message.answer(f"✅ Подпись обновлена для {selected_track}: {signature}")
            await state.clear()
            return

    await message.answer("❌ Не удалось найти трек-номер. Введите /отмена и начните заново.")
    await state.clear()

    
class TrackDeleting(StatesGroup):
    selecting_track = State()

# ✅ Удалить трек-номер
@router.message(F.text.in_(["❌ Удалить трек-номер", "/delete_track"]))
async def delete_track_handler(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    current_state = await state.get_state()
    if current_state is not None:
        await message.answer("⏳ Завершите предыдущее действие или введите /отмена.")
        return

    await state.clear()

    # Получаем треки пользователя
    user_tracks = [
        row[0].strip().upper()
        for row in tracking_sheet.get_all_values()
        if len(row) > 4 and row[4] == user_id
    ]

    if not user_tracks:
        await message.answer("📭 У вас нет активных трек-номеров.")
        return

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=track)] for track in user_tracks],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await state.set_state(TrackDeleting.selecting_track)
    await asyncio.sleep(0.1)
    await message.answer("❌ Выберите трек-номер, который хотите удалить:", reply_markup=keyboard)



@router.message(TrackDeleting.selecting_track)
async def confirm_deletion(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "/отмена", "/cancel"]:
        await cancel_handler(message, state)
        return
    
    user_id = str(message.from_user.id)
    track_to_delete = message.text.strip().upper()

    records = tracking_sheet.get_all_values()

    for i, row in enumerate(records):
        if row[0].strip().upper() == track_to_delete and len(row) > 4 and row[4] == user_id:
            tracking_sheet.delete_rows(i + 1)
            await message.answer(f"✅ Трек-номер {track_to_delete} удалён.", reply_markup=user_keyboard)
            await state.clear()
            return

    await message.answer("❌ Не удалось найти трек-номер. Пожалуйста, выберите из списка или введите /отмена.")


# ✅ /contact_manager – связь с менеджером
@router.message(F.text.in_(["📞 Связаться с менеджером", "/contact_manager"]))
async def contact_manager_handler(message: Message):
    logging.info(f"🔘 Кнопка 'Связаться с менеджером' нажата пользователем {message.from_user.id}")

    whatsapp_link = "https://wa.me/77028888252"
    text = f"📞 Свяжитесь с менеджером через [WhatsApp]({whatsapp_link})"

    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)



# ✅ /push – массовая рассылка (только для админа)
from aiogram.fsm.state import StatesGroup, State

class PushNotification(StatesGroup):
    awaiting_message = State()

@router.message(F.text == "/push")
async def start_push_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    await state.set_state(PushNotification.awaiting_message)
    await message.answer("✉️ Введите сообщение для рассылки:")

@router.message(PushNotification.awaiting_message)
async def send_push_handler(message: Message, state: FSMContext):
    push_text = message.text.strip()

    # Получаем всех пользователей из таблицы
    user_ids = users_sheet.col_values(1)  # Первый столбец — user_id
    sent_count = 0

    for user_id in user_ids[1:]:  # Пропускаем заголовок таблицы
        try:
            await bot.send_message(user_id, push_text)
            sent_count += 1
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

    await message.answer(f"✅ Уведомление отправлено {sent_count} пользователям.")
    await state.clear()  # Очищаем состояние FSM

@router.message(F.text == "/check_issued")
async def check_issued_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    issued_records = issued_sheet.get_all_values()
    tracking_records = tracking_sheet.get_all_values()
    updated_count = 0

    for i in range(len(issued_records) - 1, 0, -1):
        issued_row = issued_records[i]
        issued_track = issued_row[0].strip().lower()

        if not issued_track:
            continue

        if len(issued_row) > 1 and issued_row[1] == "✅":
            break  # Если уже помечено, пропускаем

        user_id = None
        manager_code = None
        signature = None

        # Ищем владельца трека в "Трекинг"
        for j, track_row in enumerate(tracking_records[1:], start=2):  # Пропускаем заголовок
            if issued_track == track_row[0].strip().lower():
                user_id = track_row[4]  # ID Telegram клиента
                manager_code = track_row[2]  # Код менеджера
                signature = track_row[3]  # Подпись

                # Обновляем "Код менеджера", "Подпись" и "ID Телеграма" в "Выданное"
                issued_sheet.update(f"D{i + 1}", [[manager_code]])  # Код менеджера
                issued_sheet.update(f"E{i + 1}", [[signature]])  # Подпись
                issued_sheet.update(f"F{i + 1}", [[user_id]])  # ID Телеграма

                # Меняем статус в "Трекинг" на "Выдано"
                tracking_sheet.update_cell(j, 2, "Выдано")

                issued_sheet.update_cell(i + 1, 2, "✅")  # Помечаем, что статус обновлён
                updated_count += 1
                break  # Нашли – обновили – выходим

    await message.answer(f"✅ Обновлено {updated_count} треков. Теперь они отображаются как 'Выдано'.")

@router.message(F.text == "/check_china")
async def check_china_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    china_records = china_sheet.get_all_values()
    tracking_records = tracking_sheet.get_all_values()
    found = 0

    for i in range(len(china_records) - 1, 0, -1):
        china_row = china_records[i]
        china_track = china_row[0].strip().lower()

        if not china_track:
            continue

        if len(china_row) > 1 and china_row[1] == "✅":
            break

        user_id = None
        manager_code = None
        signature = None
        date = china_row[2] if len(china_row) > 2 else None  # Берём дату, если есть

        # Ищем владельца трека в "Трекинг"
        for track_row in tracking_records[1:]:
            if china_track == track_row[0].strip().lower():
                user_id = track_row[4]  # ID Telegram клиента
                manager_code = track_row[2]  # Код менеджера
                signature = track_row[3]  # Подпись
                break

        if user_id:
            date_text = f" ({date})" if date else ""
            message_text = get_text("china_notification", track=china_track.upper()) + date_text
            await bot.send_message(user_id, message_text)
            await asyncio.sleep(0.1)

            china_sheet.update(f"D{i + 1}", [[manager_code]])
            await asyncio.sleep(0.1)

            china_sheet.update(f"E{i + 1}", [[signature]])
            await asyncio.sleep(0.1)

            china_sheet.update(f"F{i + 1}", [[user_id]])
            await asyncio.sleep(0.1)

            china_sheet.update_cell(i + 1, 2, "✅")
            await asyncio.sleep(0.1)

    await message.answer(f"✅ Отправлено {found} уведомлений! Заполнены столбцы.")


@router.message(F.text == "/check_kz")
async def check_kz_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    kz_records = kz_sheet.get_all_values()
    tracking_records = tracking_sheet.get_all_values()
    found = 0

    for i in range(len(kz_records) - 1, 0, -1):
        kz_row = kz_records[i]
        kz_track = kz_row[0].strip().lower()

        if not kz_track:
            continue

        if len(kz_row) > 1 and kz_row[1] == "✅":
            break

        user_id = None
        manager_code = None
        signature = None
        date = kz_row[2] if len(kz_row) > 2 else None  # Берём дату, если есть

        for track_row in tracking_records[1:]:
            if kz_track == track_row[0].strip().lower():
                user_id = track_row[4]  # ID Telegram клиента
                manager_code = track_row[2]  # Код менеджера
                signature = track_row[3]  # Подпись
                break

        if user_id:
            date_text = f" ({date})" if date else ""
            message_text = get_text("kz_notification", track=kz_track.upper()) + date_text
            await bot.send_message(user_id, message_text)

            # ✅ Заполняем "Код менеджера", "Подпись" и "ID Телеграма"
            kz_sheet.update(f"D{i + 1}", [[manager_code]])  # Код менеджера
            kz_sheet.update(f"E{i + 1}", [[signature]])  # Подпись
            kz_sheet.update(f"F{i + 1}", [[user_id]])  # ID Телеграма

            kz_sheet.update_cell(i + 1, 2, "✅")
            found += 1

    await message.answer(f"✅ Отправлено {found} уведомлений! Заполнены столбцы.")
    
    
# ✅ Отмена
@router.message(F.text.lower().in_(["отмена", "/cancel", "/отмена"]))
async def cancel_handler(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()

    # Безопасно удаляем активный статус, если используется
    if "active_states" in globals():
        active_states.pop(user_id, None)

    await message.answer("❌ Действие отменено.", reply_markup=user_keyboard)




# ==========================
# 🔹 Запуск бота
# ==========================
# ✅ Добавление трека в базу (обновленный код)
@router.message(lambda message: not message.text.startswith("/") and message.text not in [
    "📦 Проверить статус посылок", "🖊 Подписать трек-номер", "❌ Удалить трек-номер", "📞 Связаться с менеджером",
    "/sign_track", "/delete_track", "/contact_manager"
])
async def add_tracking_handler(message: Message, state: FSMContext):
    # 🔒 Проверка: если пользователь в процессе регистрации — не даём добавить трек
    current_state = await state.get_state()
    if current_state in [
        Registration.name.state,
        Registration.city.state,
        Registration.phone.state,
        Registration.manager_code.state
    ]:
        await message.answer("⚠️ Вы проходите регистрацию. Пожалуйста, завершите её или введите /отмена.")
        return
    
    if current_state == TrackManagement.deleting_track.state:
        await message.answer("⚠️ Сейчас вы удаляете трек-номер. Завершите это действие или введите /отмена.")
        return
    
    if current_state == TrackManagement.selecting_track.state:
        await message.answer("⚠️ Сейчас вы подписываете трек-номер. Сначала завершите это действие или введите /отмена.")
        return

        
    user_input = " ".join(message.text.split())  # Убираем лишние пробелы
    user_id = str(message.from_user.id)

    logging.info(f"📦 Получен ввод: {user_input} от {user_id}")

    if not user_input:
        await message.answer("❌ Вы не ввели трек-номер. Попробуйте ещё раз.")
        return

    # Разделяем ввод: первый элемент — трек, остальное — подпись
    parts = user_input.split(" ", 1)
    track_number = parts[0].upper()  # Первый элемент — трек-номер
    signature = parts[1] if len(parts) > 1 else ""  # Остальное — подпись (если есть)

        # Проверка длины трек-номера
    if not (8 <= len(track_number) <= 20):
        await message.answer("❌ Трек-номер должен содержать от 8 до 20 символов. Попробуйте ещё раз.")
        return

    # Проверяем, зарегистрирован ли пользователь
    id_column = users_sheet.col_values(1)
    if user_id not in id_column:
        await message.answer("⚠️ Вы не зарегистрированы! Пройдите регистрацию командой /register")
        return

    # Проверка на повторный трек
    existing_tracks = [
        row[0].strip().upper()
        for row in tracking_sheet.get_all_values()
        if len(row) > 4 and row[4] == user_id
    ]
    if track_number in existing_tracks:
        await message.answer(f"⚠️ Трек-номер {track_number} уже добавлен ранее.")
        return

    # Получаем код менеджера
    row_index = id_column.index(user_id) + 1
    manager_code = users_sheet.cell(row_index, 5).value
    current_date = datetime.now().strftime("%Y-%m-%d")

    logging.info(f"✅ Добавляю трек в Tracking: {track_number}, Подпись: {signature}")

    # Добавляем в "Трекинг"
    tracking_sheet.append_row(
        [track_number, current_date, manager_code, signature, user_id],
        value_input_option="USER_ENTERED"
    )

    await message.answer(f"✅ Трек-номер {track_number} сохранён{' с подписью: ' + signature if signature else ''}.")



# ✅ /update_texts – обновление текстов из Google Sheets (только для админа)
@router.message(F.text == "/update_texts")
async def update_texts_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    load_texts()  # Загружаем тексты заново из Google Sheets
    await message.answer("✅ Тексты обновлены!")
    
async def main():
    logging.basicConfig(level=logging.INFO)
    load_texts()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("✅ Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

@dp.errors()
async def global_error_handler(event, exception):
    from aiogram.exceptions import TelegramForbiddenError

    # Если это ошибка отправки сообщения (например, пользователь заблокировал бота)
    if isinstance(exception, TelegramForbiddenError):
        logging.warning(f"⚠️ Пользователь заблокировал бота — {event.from_user.id}")
        return True  # подавляем ошибку

    # Логируем другие ошибки
    logging.exception(f"🔥 Глобальная ошибка: {exception}")

    # Пытаемся уведомить пользователя (если это message-объект)
    try:
        if hasattr(event, "answer"):
            await event.answer("❌ Произошла ошибка, но бот продолжает работать.")
    except Exception as e:
        logging.warning(f"❌ Не удалось отправить сообщение об ошибке: {e}")

    return True  # Не передаём ошибку дальше, чтобы бот не упал
