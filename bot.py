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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import StateFilter

from logging.handlers import RotatingFileHandler

# 🔧 Настройка логирования с ротацией
log_handler = RotatingFileHandler(
    filename="bot.log",       # основной файл логов
    maxBytes=1_000_000,       # максимум 1 МБ на файл
    backupCount=5             # хранить до 5 файлов: bot.log.1, ..., bot.log.5
)

log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
log_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[log_handler]
)


# ==========================
# 🔹 Настройки бота и таблицы
# ==========================

TOKEN = "6974697621:AAHM4qa91k4nq4Hsbn-rSDTkL8-6hAsa3pA"  # Укажи свой токен прямо в коде или загрузи из переменной окружения
SHEET_ID = "1QaR920L5bZUGNLk02M-lgXr9c5_nHJQVoPgPL7UVVY4"
ADMIN_IDS = ["665932047", "473541446", "5181691179"]  # Telegram ID админа
MINI_ADMIN_IDS = ["914265474", "1285622060", "632325004",]  # ← здесь реальные Telegram ID

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

class FindTrackFSM(StatesGroup):
    waiting_suffix = State()

class FindByCodeFSM(StatesGroup):
    waiting_code = State()

class FindByPhoneFSM(StatesGroup):
    waiting_phone = State()

    # 📜 FSM для стресс-теста
class StressTestFSM(StatesGroup):
    waiting_confirmation = State()






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

MINI_ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="find_track", description="🔍 Поиск трека по цифрам"),
    BotCommand(command="find_by_code", description="🔍 Поиск треков по коду"),
    BotCommand(command="find_by_phone", description="🔍 Поиск по номеру телефона"),
    BotCommand(command="stress_test", description="💥 Проверка устойчивости бота"),
]

ADMIN_COMMANDS = MINI_ADMIN_COMMANDS + [
    BotCommand(command="check_china", description="🇨🇳 Проверить Китай"),
    BotCommand(command="check_kz", description="🇰🇿 Проверить Казахстан"),
    BotCommand(command="check_issued", description="📦 Обновить 'Выданное'"),
    BotCommand(command="push", description="📢 Массовая рассылка"),
    BotCommand(command="update_texts", description="🔄 Обновить тексты уведомлений"),
]

async def set_bot_commands():
    await bot.set_my_commands(USER_COMMANDS)  # базовые команды по умолчанию

    for mini_id in MINI_ADMIN_IDS:
        await bot.set_my_commands(MINI_ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=mini_id))

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

# ✅ Обработка отмены в любом месте
@router.message(lambda msg: msg.text and msg.text.lower() in ["отмена", "/cancel", "/отмена"])
async def cancel_handler_global(message: Message, state: FSMContext):
    await cancel_handler(message, state)
    
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
   
    await state.update_data(name=message.text.strip())
    await state.set_state(Registration.city)
    await message.answer(get_text("ask_city"))

@router.message(Registration.city)
async def register_city_handler(message: Message, state: FSMContext):
    
    await state.update_data(city=message.text.strip())
    await state.set_state(Registration.phone)
    await message.answer(get_text("ask_phone"))

@router.message(Registration.phone)
async def register_phone_handler(message: Message, state: FSMContext):
    

    await state.update_data(phone=message.text.strip())
    await state.set_state(Registration.manager_code)
    await message.answer(get_text("ask_manager_code"))

@router.message(Registration.manager_code)
async def register_manager_handler(message: Message, state: FSMContext):
   
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
            signature = row[3] if len(row) > 3 else ""

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
        signature_part = f" ({signature})" if signature != "Без подписи" else ""
        text += f"{indicator} {status}: {track_number}{date_part}{signature_part}\n"

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

    # 🛡️ Глобальные переменные (если ещё не добавлены)
is_notifying = is_notifying if 'is_notifying' in globals() else {"china": False, "kz": False}
pending_notifications = pending_notifications if 'pending_notifications' in globals() else {"china": [], "kz": []}


# ✅ Отправка уведомлений по КАЗАХСТАНУ — сначала batch-обновление, потом уведомления
async def send_kz_notifications():
    filename = "pending_kz.json"
    if not os.path.exists(filename): return

    with open(filename, "r") as f:
        notifications = json.load(f)

    updates = []
    to_notify = []
    count = 0

    for item in notifications:
        row = item["row_index"]
        updates.append({"range": f"D{row}", "values": [[item["manager_code"]]]})
        updates.append({"range": f"E{row}", "values": [[item["signature"]]]})
        updates.append({"range": f"F{row}", "values": [[item["user_id"]]]})
        updates.append({"range": f"B{row}", "values": [["✅"]]})

        to_notify.append({
            "user_id": item["user_id"],
            "text": get_text("kz_notification", track=item["track"]) + (f" ({item['date']})" if item.get("date") else "")
        })

    # 🔧 Сначала записываем все данные в таблицу
    try:
        kz_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"KZ ⚠️ Ошибка при batch-обновлении таблицы: {e}")
        return  # Не продолжаем, если запись не удалась

    # 📨 Потом шлём уведомления
    for item in to_notify:
        try:
            await bot.send_message(item["user_id"], item["text"])
            await asyncio.sleep(0.6)
            count += 1
        except Exception as e:
            logging.warning(f"KZ ❌ Ошибка при отправке {item['user_id']}: {e}")
            continue

    os.remove(filename)

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, f"✅ KZ: Оповещено {count} человек.")

@router.message(Command("check_kz"))
async def check_kz_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    if os.path.exists("pending_kz.json"):
        await message.answer("⚠️ Предыдущая рассылка ещё не завершена!")
        return

    records = kz_sheet.get_all_values()
    tracking = tracking_sheet.get_all_values()
    issued_data = issued_sheet.get_all_values()

    notif = []
    updates = []
    auto_updates = []
    cache = set()

    for i in range(len(records) - 1, 0, -1):
        row = records[i]
        track = row[0].strip().lower()
        if not track or (len(row) > 1 and row[1] in ["✅", "🟨"]):
            continue

        # Проверка: есть ли уже в "Выдано"
        in_issued = any(track == r[0].strip().lower() for r in issued_data[1:])
        user_id = manager_code = signature = None

        for t in tracking[1:]:
            if track == t[0].strip().lower():
                user_id, manager_code, signature = t[4], t[2], t[3]
                break

        if in_issued:
            # 🛠 Автоматическое закрытие без уведомлений
            if user_id:
                auto_updates += [
                    {"range": f"D{i+1}", "values": [[manager_code]]},
                    {"range": f"E{i+1}", "values": [[signature]]},
                    {"range": f"F{i+1}", "values": [[user_id]]},
                    {"range": f"B{i+1}", "values": [["✅"]]},
                ]
            continue

        # Уведомление
        date = row[2] if len(row) > 2 else ""
        if user_id:
            key = f"{user_id}:{track}"
            if key in cache:
                continue
            cache.add(key)
            notif.append({
                "row_index": i+1,
                "track": track.upper(),
                "user_id": user_id,
                "manager_code": manager_code,
                "signature": signature,
                "date": date
            })
            updates.append({"range": f"B{i+1}", "values": [["✅"]]})

    # ✅ Сначала — автообновление без уведомлений
    if auto_updates:
        try:
            kz_sheet.batch_update(auto_updates)
            await message.answer(f"✅ Казахстан: автоматически закрыто {len(auto_updates)//4} строк без уведомлений.")
        except Exception as e:
            logging.warning(f"KZ ❌ Ошибка при автообновлении: {e}")
            await message.answer("⚠️ Ошибка при автообновлении таблицы!")
            return

    # 📦 Потом — уведомления
    if not notif:
        await message.answer("📭 Новых уведомлений по Казахстану не найдено.")
        return

    try:
        kz_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"KZ Ошибка обновления таблицы: {e}")
        await message.answer("⚠️ Ошибка при обновлении таблицы!")
        return

    with open("pending_kz.json", "w") as f:
        json.dump(notif, f)

    await message.answer(f"✅ Казахстан: найдено {len(notif)} человек. Рассылка началась...")
    asyncio.create_task(send_kz_notifications())

# ✅ Отправка уведомлений по КИТАЮ — сначала batch-обновление, потом сообщения
async def send_china_notifications():
    filename = "pending_china.json"
    if not os.path.exists(filename): return

    with open(filename, "r") as f:
        notifications = json.load(f)

    updates = []
    to_notify = []
    count = 0

    for item in notifications:
        row = item["row_index"]
        updates.append({"range": f"D{row}", "values": [[item["manager_code"]]]})
        updates.append({"range": f"E{row}", "values": [[item["signature"]]]})
        updates.append({"range": f"F{row}", "values": [[item["user_id"]]]})
        updates.append({"range": f"B{row}", "values": [["✅"]]})

        to_notify.append({
            "user_id": item["user_id"],
            "text": get_text("china_notification", track=item["track"]) + (f" ({item['date']})" if item.get("date") else "")
        })

    # 🔧 Сначала записываем все данные в таблицу
    try:
        china_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"CN ⚠️ Ошибка при batch-обновлении таблицы: {e}")
        return  # Не продолжаем, если запись не удалась

    # 📨 Потом шлём уведомления
    for item in to_notify:
        try:
            await bot.send_message(item["user_id"], item["text"])
            await asyncio.sleep(0.6)
            count += 1
        except Exception as e:
            logging.warning(f"CN ❌ Ошибка при отправке {item['user_id']}: {e}")
            continue

    os.remove(filename)

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, f"✅ CN: Оповещено {count} человек.")

@router.message(Command("check_china"))
async def check_china_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    if os.path.exists("pending_china.json"):
        await message.answer("⚠️ Предыдущая рассылка ещё не завершена!")
        return

    records = china_sheet.get_all_values()
    tracking = tracking_sheet.get_all_values()
    kz_data = kz_sheet.get_all_values()
    issued_data = issued_sheet.get_all_values()

    notif = []
    updates = []
    auto_updates = []
    cache = set()

    for i in range(len(records) - 1, 0, -1):
        row = records[i]
        track = row[0].strip().lower()
        if not track or (len(row) > 1 and row[1] in ["✅", "🟨"]):
            continue

        # Проверяем: уже в КЗ или Выдано?
        in_next_stage = any(track == r[0].strip().lower() for r in kz_data[1:] + issued_data[1:])
        user_id = manager_code = signature = None

        for t in tracking[1:]:
            if track == t[0].strip().lower():
                user_id, manager_code, signature = t[4], t[2], t[3]
                break

        if in_next_stage:
            # ⛔ Автозакрытие без уведомлений
            if user_id:
                auto_updates += [
                    {"range": f"D{i+1}", "values": [[manager_code]]},
                    {"range": f"E{i+1}", "values": [[signature]]},
                    {"range": f"F{i+1}", "values": [[user_id]]},
                    {"range": f"B{i+1}", "values": [["✅"]]},
                ]
            continue

        # Если надо уведомить — добавляем в список
        date = row[2] if len(row) > 2 else ""
        if user_id:
            key = f"{user_id}:{track}"
            if key in cache:
                continue
            cache.add(key)

            notif.append({
                "row_index": i+1,
                "track": track.upper(),
                "user_id": user_id,
                "manager_code": manager_code,
                "signature": signature,
                "date": date
            })
            updates.append({"range": f"B{i+1}", "values": [["✅"]]})

    # 💥 Сначала batch-обновление автозакрытых
    if auto_updates:
        try:
            china_sheet.batch_update(auto_updates)
            await message.answer(f"✅ Китай: автоматически закрыто {len(auto_updates)//4} строк без уведомлений.")
        except Exception as e:
            logging.warning(f"CN ❌ Ошибка при автообновлении: {e}")
            await message.answer("⚠️ Ошибка при автообновлении таблицы!")
            return

    # 📦 Теперь уведомления
    if not notif:
        await message.answer("📭 Новых уведомлений по Китаю не найдено.")
        return

    try:
        china_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"CN Ошибка обновления таблицы: {e}")
        await message.answer("⚠️ Ошибка при обновлении таблицы!")
        return

    with open("pending_china.json", "w") as f:
        json.dump(notif, f)

    await message.answer(f"✅ Китай: найдено {len(notif)} человек. Рассылка началась...")
    asyncio.create_task(send_china_notifications())

# ✅ Обновление статуса по ВЫДАННЫМ — batch-обновление без уведомлений
async def send_issued_updates():
    filename = "pending_issued.json"
    if not os.path.exists(filename): return

    with open(filename, "r") as f:
        notifications = json.load(f)

    updates = []
    count = 0

    for item in notifications:
        row = item["row_index"]
        updates.append({"range": f"D{row}", "values": [[item["manager_code"]]]})
        updates.append({"range": f"E{row}", "values": [[item["signature"]]]})
        updates.append({"range": f"F{row}", "values": [[item["user_id"]]]})
        updates.append({"range": f"B{row}", "values": [["✅"]]})
        count += 1

    try:
        issued_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"ISS ⚠️ Ошибка при batch-обновлении таблицы: {e}")
        return

    os.remove(filename)

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, f"✅ ISSUED: Обновлено {count} строк без рассылки сообщений.")

@router.message(Command("check_issued"))
async def check_issued_handler(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    if os.path.exists("pending_issued.json"):
        await message.answer("⚠️ Предыдущая обработка ещё не завершена!")
        return

    records = issued_sheet.get_all_values()
    tracking = tracking_sheet.get_all_values()

    notif = []
    updates = []
    cache = set()

    for i in range(len(records) - 1, 0, -1):
        row = records[i]
        track = row[0].strip().lower()
        if not track or (len(row) > 1 and row[1] in ["✅", "🟨"]):
            continue

        user_id = manager_code = signature = None

        for t in tracking[1:]:
            if track == t[0].strip().lower():
                user_id, manager_code, signature = t[4], t[2], t[3]
                break

        if user_id:
            key = f"{user_id}:{track}"
            if key in cache:
                continue
            cache.add(key)
            notif.append({
                "row_index": i+1,
                "track": track.upper(),
                "user_id": user_id,
                "manager_code": manager_code,
                "signature": signature
            })
            updates.append({"range": f"B{i+1}", "values": [["✅"]]})

    if not notif:
        await message.answer("📭 Новых записей в 'Выданное' не найдено.")
        return

    try:
        issued_sheet.batch_update(updates)
    except Exception as e:
        logging.warning(f"ISS Ошибка обновления таблицы: {e}")
        await message.answer("⚠️ Ошибка при обновлении таблицы!")
        return

    with open("pending_issued.json", "w") as f:
        json.dump(notif, f)

    await message.answer(f"✅ Выданное: найдено {len(notif)} строк. Обновление таблицы началось...")
    asyncio.create_task(send_issued_updates())

    
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
@router.message(
    StateFilter(None),
    lambda message: not message.text.startswith("/") and message.text not in [
        "📦 Проверить статус посылок", "🖊 Подписать трек-номер", "❌ Удалить трек-номер", "📞 Связаться с менеджером",
        "/sign_track", "/delete_track", "/contact_manager"
    ]
)
async def add_tracking_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()

    # 💥 Если пользователь сейчас в FSM (любой), выходим
    if current_state is not None:
        return

    # 🔒 Защита от регистрации, если вдруг фильтр не сработал
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
    
# ✅ Поиск по трек-номеру (для админа)
@router.message(Command("find_track"))
async def find_track_command(message: Message, state: FSMContext):
    if str(message.from_user.id) not in MINI_ADMIN_IDS + ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    await state.set_state(FindTrackFSM.waiting_suffix)
    await message.answer("🔍 Введите часть трек-номера (от 4 до 25 символов):")


@router.message(FindTrackFSM.waiting_suffix)
async def process_track_suffix(message: Message, state: FSMContext):
    suffix = message.text.strip().lower()
    await state.clear()

    if not suffix.isalnum() or len(suffix) < 4 or len(suffix) > 25:
        await message.answer("⚠️ Введите от 4 до 25 символов (только буквы и цифры).")
        return

    def search_table(sheet, label, status_text):
        results = []
        records = sheet.get_all_values()[1:]
        for i, row in enumerate(records):
            if len(row) > 0 and suffix in row[0].strip().lower():
                results.append({
                    "track": row[0].strip().upper(),
                    "status": status_text,
                    "date": row[2] if len(row) > 2 else "",
                    "manager_code": row[3] if len(row) > 3 else "",
                    "signature": row[4] if len(row) > 4 else "",
                    "user_id": row[5] if len(row) > 5 else None
                })
        return results

    results = []
    results += search_table(issued_sheet, "Выданное", "Выдано")
    results += search_table(kz_sheet, "Казахстан", "На складе в КЗ")
    results += search_table(china_sheet, "Китай", "В пути до Алматы")

    if not results:
        await message.answer("📭 Совпадений не найдено.")
        return

    seen = set()
    filtered = []
    for item in results:
        if item["track"] in seen:
            continue
        seen.add(item["track"])
        filtered.append(item)

    for item in filtered:
        text = (
            f"🔸 `{item['track']}`\n"
            f"📍 Статус: *{item['status']}*\n"
        )
        if item["date"]:
            text += f"📅 Дата: {item['date']}\n"
        if item["manager_code"]:
            text += f"🔑 Индивидуальный код: {item['manager_code']}\n"
        if item["signature"]:
            text += f"✏️ Подпись: {item['signature']}\n"
        if item["user_id"]:
            text += f"🆔 ID: {item['user_id']}"

        buttons = [
            [InlineKeyboardButton(text="📋 Скопировать", callback_data=f"copy:{item['track']}")]
        ]
        if item["user_id"]:
            buttons[0].append(InlineKeyboardButton(text="📤 Отправить клиенту", callback_data=f"send:{item['user_id']}:{item['track']}"))

        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)



# ✅ Обработка inline-кнопок
@router.callback_query(F.data.startswith("copy:"))
async def handle_copy_button(callback: CallbackQuery):
    track = callback.data.split(":", 1)[1]
    await callback.answer("✅ Скопируйте трек-номер")
    await callback.message.answer(f"📋 Трек для копирования: `{track}`", parse_mode="Markdown")

@router.callback_query(F.data.startswith("send:"))
async def handle_send_to_client(callback: CallbackQuery):
    _, user_id, track = callback.data.split(":")
    user_id = int(user_id)

    # Ищем этот трек во всех таблицах
    all_sheets = [
        (issued_sheet, "Выдано"),
        (kz_sheet, "На складе в КЗ"),
        (china_sheet, "В пути до Алматы")
    ]

    full_info = None
    for sheet, status in all_sheets:
        records = sheet.get_all_values()[1:]
        for row in records:
            if len(row) > 0 and row[0].strip().upper() == track.upper():
                full_info = {
                    "track": track,
                    "status": status,
                    "date": row[2] if len(row) > 2 else "",
                    "manager_code": row[3] if len(row) > 3 else "",
                    "signature": row[4] if len(row) > 4 else ""
                }
                break
        if full_info:
            break

    if not full_info:
        await callback.answer("❌ Трек не найден.")
        return

    # Формируем текст для клиента
    text = f"📦 Обновление по вашему трек-номеру:\n\n"
    text += f"🔸 `{full_info['track']}`\n"
    text += f"📍 Статус: *{full_info['status']}*\n"
    if full_info["date"]:
        text += f"📅 Дата: {full_info['date']}\n"
    if full_info["manager_code"]:
        text += f"🔑 Индивидуальный код: {full_info['manager_code']}\n"
    if full_info["signature"]:
        text += f"✏️ Подпись: {full_info['signature']}"

    try:
        await bot.send_message(user_id, text, parse_mode="Markdown")
        await callback.answer("📤 Уведомление отправлено клиенту")
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при отправке клиенту: {e}")


# ✅ Команда /find_by_code — найти все треки по индивидуальному коду
@router.message(Command("find_by_code"))
async def find_by_code_command(message: Message, state: FSMContext):
    if str(message.from_user.id) not in MINI_ADMIN_IDS + ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    await state.set_state(FindByCodeFSM.waiting_code)
    await message.answer("🔍 Введите индивидуальный код клиента:")

@router.message(FindByCodeFSM.waiting_code)
async def process_code(message: Message, state: FSMContext):
    manager_code = message.text.strip().lower()  # 🟢 привели к нижнему регистру
    await state.clear()

    # Поиск Telegram ID клиента по таблице пользователей
    user_records = users_sheet.get_all_values()[1:]
    user_id = None
    for row in user_records:
        if len(row) > 4 and row[4].strip().lower() == manager_code:  # 🟢 сравнение в нижнем регистре
            user_id = row[0].strip()
            break

    if not user_id:
        await message.answer("❌ Пользователь с таким кодом не найден в таблице пользователей.")
        return

    # ... остальная логика по отправке результатов

    def search_by_user(sheet, status_text):
        results = []
        rows = sheet.get_all_values()[1:]
        for row in rows:
            if len(row) > 5 and row[5].strip() == user_id:
                results.append({
                    "track": row[0].strip().upper(),
                    "status": status_text,
                    "date": row[2] if len(row) > 2 else "",
                    "signature": row[4] if len(row) > 4 else ""
                })
        return results

    tracks = []
    seen = set()
    for sheet, label in [
        (issued_sheet, "Выдано"),
        (kz_sheet, "На складе в КЗ"),
        (china_sheet, "В пути до Алматы")
    ]:
        results = search_by_user(sheet, label)
        for item in results:
            if item["track"] not in seen:
                seen.add(item["track"])
                tracks.append(item)

    if not tracks:
        await message.answer("📭 У клиента нет активных треков в таблицах.")
        return

    # Формируем общий текст
    text = f"🔎 Найдено {len(tracks)} треков по коду: {manager_code}\n🆔 Telegram ID клиента: {user_id}\n"
    for item in tracks:
        text += f"\n— `{item['track']}`\n📍 Статус: *{item['status']}*\n"
        if item["date"]:
            text += f"📅 Дата: {item['date']}\n"
        if item["signature"]:
            text += f"✏️ Подпись: {item['signature']}\n"

    # Кнопки снизу
    buttons = [
        [InlineKeyboardButton(text="📋 Скопировать всё", callback_data=f"copyall:{manager_code}")]
    ]
    if user_id:
        buttons[0].append(InlineKeyboardButton(
            text="📤 Отправить клиенту", callback_data=f"sendall:{user_id}:{manager_code}"
        ))
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(text, parse_mode="Markdown", reply_markup=markup)

# 📋 Обработка копирования всех треков
@router.callback_query(F.data.startswith("copyall:"))
async def handle_copy_all(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    await callback.answer("📋 Скопируйте список треков")
    await callback.message.answer(f"Код клиента: `{code}`", parse_mode="Markdown")

# 📤 Отправка клиенту всего списка треков
@router.callback_query(F.data.startswith("sendall:"))
async def handle_send_all(callback: CallbackQuery):
    _, user_id, code = callback.data.split(":")
    user_id = int(user_id)

    # Получаем все треки клиента по этому ID
    all_sheets = [
        (issued_sheet, "Выдано"),
        (kz_sheet, "На складе в КЗ"),
        (china_sheet, "В пути до Алматы")
    ]

    tracks = []
    seen = set()
    for sheet, status in all_sheets:
        records = sheet.get_all_values()[1:]
        for row in records:
            if len(row) > 5 and row[5].strip() == str(user_id):
                track = row[0].strip().upper()
                if track not in seen:
                    seen.add(track)
                    tracks.append({
                        "track": track,
                        "status": status,
                        "date": row[2] if len(row) > 2 else "",
                        "signature": row[4] if len(row) > 4 else ""
                    })

    if not tracks:
        await callback.message.answer("❌ У клиента нет активных треков.")
        return

    # Формируем текст для клиента
    text = f"📦 Обновление по вашим трекам (код: `{code}`):\n"
    for item in tracks:
        text += f"\n🔸 `{item['track']}`\n📍 Статус: *{item['status']}*\n"
        if item["date"]:
            text += f"📅 Дата: {item['date']}\n"
        if item["signature"]:
            text += f"✏️ Подпись: {item['signature']}\n"

    try:
        await bot.send_message(user_id, text, parse_mode="Markdown")
        await callback.answer("📤 Уведомление отправлено клиенту")
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при отправке клиенту: {e}")

# ✅ Команда /find_by_phone — найти все треки по номеру телефона
@router.message(Command("find_by_phone"))
async def find_by_phone_command(message: Message, state: FSMContext):
    if str(message.from_user.id) not in MINI_ADMIN_IDS + ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return

    await state.set_state(FindByPhoneFSM.waiting_phone)
    await message.answer("🔍 Введите номер телефона клиента (в любом формате):")


@router.message(FindByPhoneFSM.waiting_phone)
async def process_phone(message: Message, state: FSMContext):
    import re
    await state.clear()

    def normalize_last9(text):
        digits = re.sub(r"\D", "", text)
        return digits[-9:]

    phone_input = message.text.strip()
    phone_clean = normalize_last9(phone_input)

    user_records = users_sheet.get_all_values()[1:]
    user_id = None
    manager_code = None

    for row in user_records:
        if len(row) > 3:
            row_phone = row[3]
            row_clean = normalize_last9(row_phone)
            if row_clean == phone_clean:
                user_id = row[0].strip()
                manager_code = row[4].strip() if len(row) > 4 else ""
                break

    if not user_id:
        await message.answer("❌ Клиент с таким номером телефона не найден.")
        return

    def search_by_user(sheet, status_text):
        results = []
        rows = sheet.get_all_values()[1:]
        for row in rows:
            if len(row) > 5 and row[5].strip() == user_id:
                results.append({
                    "track": row[0].strip().upper(),
                    "status": status_text,
                    "date": row[2] if len(row) > 2 else "",
                    "signature": row[4] if len(row) > 4 else ""
                })
        return results

    tracks = []
    seen = set()
    for sheet, label in [
        (issued_sheet, "Выдано"),
        (kz_sheet, "На складе в КЗ"),
        (china_sheet, "В пути до Алматы")
    ]:
        results = search_by_user(sheet, label)
        for item in results:
            if item["track"] not in seen:
                seen.add(item["track"])
                tracks.append(item)

    if not tracks:
        await message.answer("📭 У клиента нет активных треков в таблицах.")
        return

    text = f"🔎 Найдено {len(tracks)} треков по номеру: {phone_input}\n🆔 Telegram ID клиента: {user_id}\n"
    for item in tracks:
        text += f"\n— `{item['track']}`\n📍 Статус: *{item['status']}*\n"
        if item["date"]:
            text += f"📅 Дата: {item['date']}\n"
        if item["signature"]:
            text += f"✏️ Подпись: {item['signature']}\n"

    buttons = [
        [InlineKeyboardButton(text="📋 Скопировать всё", callback_data=f"copyall_phone:{user_id}")],
        [InlineKeyboardButton(text="📤 Отправить клиенту", callback_data=f"sendall_phone:{user_id}:{manager_code or 'unknown'}")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(text, parse_mode="Markdown", reply_markup=markup)

@router.callback_query(F.data.startswith("copyall_phone:"))
async def handle_copyall_phone(callback: CallbackQuery):
    user_id = callback.data.split(":")[1]
    await callback.answer("📋 Скопируйте список треков")
    await callback.message.answer(f"Telegram ID клиента: `{user_id}`", parse_mode="Markdown")


@router.callback_query(F.data.startswith("sendall_phone:"))
async def handle_sendall_phone(callback: CallbackQuery):
    _, user_id, code = callback.data.split(":")
    user_id = int(user_id)

    all_sheets = [
        (issued_sheet, "Выдано"),
        (kz_sheet, "На складе в КЗ"),
        (china_sheet, "В пути до Алматы")
    ]

    tracks = []
    seen = set()
    for sheet, status in all_sheets:
        records = sheet.get_all_values()[1:]
        for row in records:
            if len(row) > 5 and row[5].strip() == str(user_id):
                track = row[0].strip().upper()
                if track not in seen:
                    seen.add(track)
                    tracks.append({
                        "track": track,
                        "status": status,
                        "date": row[2] if len(row) > 2 else "",
                        "signature": row[4] if len(row) > 4 else ""
                    })

    if not tracks:
        await callback.message.answer("❌ У клиента нет активных треков.")
        return

    text = f"📦 Обновление по вашим трекам (по номеру телефона):\n"
    for item in tracks:
        text += f"\n🔸 `{item['track']}`\n📍 Статус: *{item['status']}*\n"
        if item["date"]:
            text += f"📅 Дата: {item['date']}\n"
        if item["signature"]:
            text += f"✏️ Подпись: {item['signature']}\n"

    try:
        await bot.send_message(user_id, text, parse_mode="Markdown")
        await callback.answer("📤 Уведомление отправлено клиенту")
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при отправке клиенту: {e}")

# 🧨 Команда /stress_test
@router.message(Command("stress_test"))
async def stress_test_command(message: Message, state: FSMContext):
    if str(message.from_user.id) not in ADMIN_IDS + MINI_ADMIN_IDS:
        await message.answer("❌ Эта команда только для админов и мини-админов!")
        return

    await state.set_state(StressTestFSM.waiting_confirmation)
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Проверить устойчивость бота", callback_data="stress_yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="stress_no")
        ]
    ])
    await message.answer("Вы действительно хотите начать стресс-тест стабильности бота?", reply_markup=buttons)

# 🚫 Отмена
@router.callback_query(F.data == "stress_no")
async def cancel_stress_test(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.clear()

# 💥 Запуск фейкового краша
@router.callback_query(F.data == "stress_yes")
async def launch_stress_test(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🧪 НАЧИНАЮ АНАЛИЗ СТАБИЛЬНОСТИ БОТА...")
    await asyncio.sleep(12)

    messages = [
        "❗️ **КРИТИЧЕСКАЯ ОШИБКА: НЕУДАЛОСЬ ЗАГРУЗИТЬ core.memory**",
        "⚠️ **AIROUTER НАРУШЕН: СТРЕСС-РЕЖИМ АКТИВИРОВАН**",
        "🚫 **ПОДКЛЮЧЕНИЕ К TELEGRAM API ПРОПАЛО**",
        "💣 **БОТ НЕ МОЖЕТ ПЕРЕЗАПУСТИТЬСЯ — КОД ОШИБКИ 127**",
        "📉 **УТЕЧКА ПАМЯТИ В ОБЛАСТИ СЕРВИСА**",
        "🔒 **СИСТЕМА АВТОРИЗАЦИИ ОТКЛЮЧЕНА**",
        "💀 **ВКЛЮЧЁН АВАРИЙНЫЙ РЕЖИМ**",
        "🧨 **УДАЛЕНИЕ ВСЕХ ДАННЫХ НАЧАТО...**"
    ]

    for msg in messages:
        await callback.message.answer(msg, parse_mode="Markdown")
        await asyncio.sleep(1)

    await asyncio.sleep(7)
    await callback.message.answer("😄 Это была шутка! Бот работает стабильно. Но ты неплохо занервничал 😁")


async def main():
    load_texts()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("✅ Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


