import gspread
from google.oauth2.service_account import Credentials

# Подключаемся к Google Sheets
SHEET_ID = "1YvPF_yVecYhjFAwL8IuKAlgUv_cBJXMM4A_Xsv3s3iE"  # Твой ID таблицы
JSON_KEYFILE = r"C:\Users\User\Desktop\данные по боту\tlcbot-452706-b32f93bd688d.json"  # Полный путь к JSON-файлу

# Указываем права доступа
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file(JSON_KEYFILE, scopes=scope)
client = gspread.authorize(creds)

# Открываем таблицу
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.sheet1  # Первая вкладка

# Тест: Добавляем строку в таблицу
sheet.append_row(["Тестовый трек", "Ожидается", "2025-03-04"])
print("✅ Данные успешно добавлены в таблицу!")
