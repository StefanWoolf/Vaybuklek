import os
import sys
from pathlib import Path

# Делаем пакет dirizher импортируемым из src без установки
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Изоляция тестов от боевого .env: принудительно mock-режим, без сети.
# Переменные окружения имеют приоритет над файлом .env в pydantic-settings.
os.environ["DIRIZHER_LLM__PROVIDER"] = "mock"
os.environ["DIRIZHER_LLM__GROQ_API_KEY"] = ""
os.environ["DIRIZHER_LLM__GIGACHAT_CREDENTIALS"] = ""
os.environ["DIRIZHER_YOUGILE__API_KEY"] = ""
os.environ["DIRIZHER_TELEGRAM__BOT_TOKEN"] = ""
os.environ["DIRIZHER_AUDIO__ENABLED"] = "false"
os.environ["DIRIZHER_MEMORY__BACKEND"] = "lexical"
os.environ["DIRIZHER_LLM__CONFIDENCE_THRESHOLD"] = "0.7"
