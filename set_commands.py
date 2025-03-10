import asyncio
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

# 🔹 Твой токен бота
TOKEN = "7537026112:AAEWPikFWldtFWKeyer7_iiH793rWApLc2U"
ADMIN_ID = "665932047"

bot = Bot(token=TOKEN)

# 🔹 Команды для пользователей
USER_COMMANDS = [
    BotCommand(command="register", description="📝 Регистрация"),
    BotCommand(command="check_status", description="📦 Проверить статус посылок"),
    BotCommand(command="sign_track", description="🖊 Подписать трек-номер"),
    BotCommand(command="delete_track", description="❌ Удалить трек-номер"),
    BotCommand(command="contact_manager", description="📞 Связаться с менеджером"),
]

# 🔹 Команды для админа
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="check_china", description="🇨🇳 Проверить Китай"),
    BotCommand(command="check_kz", description="🇰🇿 Проверить Казахстан"),
    BotCommand(command="check_issued", description="📦 Обновить 'Выданное'"),
    BotCommand(command="update_texts", description="🔄 Обновить тексты уведомлений"),  # ✅ Добавлено
]

async def set_bot_commands():
    await bot.set_my_commands(USER_COMMANDS)
    await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

async def main():
    await set_bot_commands()
    print("✅ Команды установлены!")
    await bot.session.close()  # Закрываем сессию, чтобы избежать предупреждений

if __name__ == "__main__":
    asyncio.run(main())
