from dotenv import load_dotenv
import phonenumbers
from phonenumbers import carrier
from phonenumbers.phonenumberutil import NumberParseException
from fastapi import (
    HTTPException,
    status,
    UploadFile
)
from starlette.datastructures import UploadFile as StarletteUploadFile
from typing import Union
import filetype

load_dotenv()

image_types = ["jpg", "jpeg", "png", "webp"]
mtn_numbers = ["024", "054", "055", "059", "053", "025"]
vodafone_numbers = ["020", "050"]
at_numbers = ["027", "057", "056", "026"]

async def validate_file(
    file: Union[UploadFile, StarletteUploadFile],
    expected_types: str
):
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No {expected_types} file was provided."
        )
    
    max_file_size_bytes = 10 * 1024 * 1024

    if file is None:
        return
    
    contents = await file.read()
    await file.seek(0)
    

    if len(contents) > max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{expected_types} file size exceeds the 10MB limit."
        )
    
    kind = filetype.guess(contents)
    if kind is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not determine {expected_types} file type."
        )

    if kind.mime not in image_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {expected_types} type: {kind.mime}. Allowed: {image_types}"
        )

    return kind.mime


def is_valid_ghana_number(phone_number: str):
    valid_prefixes = mtn_numbers + vodafone_numbers + at_numbers
    try:
        parsed = phonenumbers.parse(phone_number, "GH")
        if not phonenumbers.is_valid_number(parsed):
            national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL).replace(" ", "")
            prefix = national[:3]
            if prefix not in valid_prefixes or len(national) != 10:
                return False
        return True
    except NumberParseException:
        return False
    

def replace_user_id(user):
    user["id"] = str(user["_id"])
    del user["_id"]
    return user