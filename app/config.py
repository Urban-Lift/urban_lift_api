import os
from supabase import create_client, Client
from pathlib import Path
from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".env"

load_dotenv(dotenv_path=env_path)

url: str = os.environ["SUPABASE_URL"]
key: str = os.environ["SUPABASE_ANON_KEY"]
supabase: Client = create_client(url, key)