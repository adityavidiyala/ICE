import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

# Get the directory where config.py actually lives
BASE_DIR = Path(__file__).parent.absolute()
env_path = BASE_DIR / ".env"

# Force load_dotenv to use that specific file
load_dotenv(dotenv_path=env_path)

class Config:
    API_KEY = os.getenv("GEMINI_API_KEY", "")
    # Preference only: startup checks Google's live catalog before selecting it.
    MODEL = os.getenv("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
