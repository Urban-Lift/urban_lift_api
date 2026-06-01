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