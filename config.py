import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMINS = [int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x]
GROUPS = [int(x) for x in os.getenv("GROUPS", "").split(",") if x]
DEFAULT_ATTEMPTS = int(os.getenv("DEFAULT_ATTEMPTS", 3))