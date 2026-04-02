from supabase import create_client
import os
from pathlib import Path
from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".env"

load_dotenv(dotenv_path=env_path)

supabase_url = os.environ["SUPABASE_URL"]
supabase_anon_key = os.environ["SUPABASE_ANON_KEY"]
supabase_service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(supabase_url, supabase_anon_key)
supabase_admin = create_client(supabase_url, supabase_service_role_key)