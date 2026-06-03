from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    status,
    UploadFile,
    File,
    Depends,
    Header,
)
from typing import Annotated, cast
from enum import Enum
from supabase_auth import User
from db.config import supabase, create_client
from admin_client import supabase, supabase_admin
from pydantic import EmailStr
from utils import is_valid_ghana_number, validate_file
from dependecies.authz import has_role
from dependecies.authn import get_current_user
import os
import re
from urllib.parse import quote

drivers_router = APIRouter(tags=["Drivers"])


url = os.getenv("SUPABASE_URL")
bucket = os.getenv("SUPABASE_BUCKET")
anon_key = os.getenv("SUPABASE_ANON_KEY")





@drivers_router.post(
    "/drivers/registration/create", dependencies=[Depends(has_role(["driver"]))]
)
async def driver_car_registration(
    current_user: Annotated[User, Depends(get_current_user)],
    full_name: Annotated[str, Form()],
    phone_number: Annotated[str, Form()],
    email: Annotated[EmailStr, Form()],
    ghana_card: Annotated[str, Form()],
    license_plate_num: Annotated[str, Form()],
    car_model: Annotated[str, Form()],
    car_color: Annotated[str, Form()],
    car_year: Annotated[int, Form()],
    card_image: list[UploadFile] = File(),
    driver_license: UploadFile = File(),
    vehicle_insurance: UploadFile = File(),
    car_pic: list[UploadFile] = File(),
):
    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

    user_id = current_user.id
    # 0239237162
    card_urls = []
    license_url = None
    insurance_url = None
    car_pic_urls = []
    try:
        for card in card_image:
            if card and card.filename:
                file_path = f"{user_id}/{card.filename}"
                file_content = await card.read()
                content_type = card.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            card_urls.append(f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}")

        if driver_license and driver_license.filename:
            file_path = f"{user_id}/{driver_license.filename}"
            file_content = await driver_license.read()
            content_type = driver_license.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            license_url = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"

        if vehicle_insurance and vehicle_insurance.filename:
            file_path = f"{user_id}/{vehicle_insurance.filename}"
            file_content = await vehicle_insurance.read()
            content_type = vehicle_insurance.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            insurance_url = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"

        for pic in car_pic:
            if pic and pic.filename:
                file_path = f"{user_id}/{pic.filename}"
                file_content = await pic.read()
                content_type = pic.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            car_pic_urls.append(f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}")
    except Exception as e:
        error_text = str(e)
        match = re.search(r"'statusCode':\s*(\d+)", error_text)
        status_code = int(match.group(1)) if match else 500
        raise HTTPException(
            status_code=status_code, detail=f"File upload failed: {str(e)}"
        )

    registration_details = {
        "auth_id": user_id,
        "full_name": full_name,
        "email": email,
        "phone_number": phone_number,
        "ghana_card": ghana_card,
        "license_plate_num": license_plate_num,
        "car_model": car_model,
        "car_color": car_color,
        "car_year": car_year,
        "card_image_urls": card_urls,
        "driver_license_url": license_url,
        "vehicle_insurance_url": insurance_url,
        "car_pic_urls": car_pic_urls,
        "approved": False
    }
    try:
        
        supabase.table("driver_car_registration").insert(registration_details).execute()
        return {"message": "Driver and car registered successfully!"}
    except Exception as db_err:
        raise HTTPException(
            status_code=500, detail=f"Database update failed: {str(db_err)}"
        )


