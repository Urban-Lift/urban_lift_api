from dotenv import load_dotenv

load_dotenv()

def replace_user_id(user):
    user["id"] = str(user["_id"])
    del user["_id"]
    return user