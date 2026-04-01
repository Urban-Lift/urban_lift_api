from dotenv import load_dotenv
import phonenumbers
from phonenumbers import carrier
from phonenumbers.phonenumberutil import NumberParseException

load_dotenv()

def generate_verification_code(length: int = 6):
    return '.join(secrets.choice(string.digits) for _ in range(length))'

def is_valid_ghana_number(phone_number: str):
    try:
        parsed = phonenumbers.parse(phone_number, "GH")
        if not phonenumbers.is_valid_number(parsed):
            return False
        network = carrier.name_for_number(parsed, "en")
        return bool(network)
    except NumberParseException:
        return False
    

def replace_user_id(user):
    user["id"] = str(user["_id"])
    del user["_id"]
    return user