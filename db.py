import os
from sqlmodel import create_engine

# Bothost сохраняет данные ТОЛЬКО в /app/data
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'database.db')

# Создаем папку если нет
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(DATABASE_URL, echo=False)

def init():
    pass