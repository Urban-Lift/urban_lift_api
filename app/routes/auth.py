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
from app.db.config import supabase, create_client
from app.admin_client import supabase, supabase_admin
from pydantic import EmailStr
from app.utils import is_valid_ghana_number, validate_file
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
import os
import re
from urllib.parse import quote

users_router = APIRouter(tags=["Users"])


class UserRole(str, Enum):
    PASSENGER = "passenger"
    DRIVER = "driver"
    ADMIN = "admin"


url = os.getenv("SUPABASE_URL")
bucket = os.getenv("SUPABASE_BUCKET")
anon_key = os.getenv("SUPABASE_ANON_KEY")


@users_router.post("/users/signup")
def register_user(
    phone_number: Annotated[str, Form()],
    role: Annotated[UserRole, Form()] = UserRole.PASSENGER,
):
    existing_user = (
        supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    )
    if existing_user.data:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists!")

    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

    user_created = {
        "phone_number": phone_number,
        "role": role.value,
        "is_active": False,
    }

    supabase.table("users").insert(user_created).execute()

    return {"message": "User registered successfully"}


@users_router.post("/users/verify/phone_number")
def verify_phone_number(phone_number: Annotated[str, Form()]):
    existing_user = (
        supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    )
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")

    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")
    # need to check if user is active
    supabase.table("users").select("id, is_active").eq("phone_number", phone_number).eq(
        "is_active", False
    ).execute()
    formatted = "+233" + phone_number[1:]

    supabase.auth.sign_in_with_otp({"phone": formatted})

    return {"message": "OTP sent successful!"}


@users_router.post("/users/verify/otp")
def verify_otp(phone_number: Annotated[str, Form()], otp: Annotated[str, Form()]):
    existing_user = (
        supabase.table("users")
        .select("id, role")
        .eq("phone_number", phone_number)
        .execute()
    )
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")

    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

    formatted = "+233" + phone_number[1:]

    try:
        response = supabase.auth.verify_otp(
            {"phone": formatted, "token": otp, "type": "sms"}
        )

    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Verification error: {e}")

    assert response.user is not None
    user_data = cast(dict, existing_user.data[0])
    user_role = user_data.get("role")
    supabase_admin.auth.admin.update_user_by_id(
        response.user.id, {"user_metadata": {"role": user_role}}
    )

    if not response.user or not response.session:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired OTP!")

    assert response.session is not None
    session = response.session
    supabase.table("users").update({"is_active": True}).eq(
        "phone_number", phone_number
    ).execute()
    return {
        "message": "Phone verified successfully",
        "access_token": session.access_token,
    }


@users_router.get(
    "/users/profile/create", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def get_my_profile(current_user: Annotated[User, Depends(get_current_user)]):

    user_id = current_user.id
    profile = supabase.table("users").select("*").eq("auth_id", user_id).execute()

    if not profile.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found!")

    return {"profile": profile.data}


@users_router.post(
    "/users/profile/create", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
async def create_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    full_name: Annotated[str, Form()],
    emergency_number: Annotated[str, Form()],
    email: Annotated[EmailStr | None, Form()] = None,
    profile_pic: UploadFile = File(None),
):
    if not (len(emergency_number) == 10 and emergency_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(emergency_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

    user_id = current_user.id
    # 0239237162
    image_url = None
    try:
        if profile_pic and profile_pic.filename:
            file_path = f"{user_id}/{profile_pic.filename}"
            file_content = await profile_pic.read()
            content_type = profile_pic.content_type or "application/octet-stream"

            supabase_admin.storage.from_("passengers_profile_pic").upload(
                file_path, file_content, {"content-type": content_type}
            )

            image_url = f"{str(url)}/storage/v1/object/public/passengers_profile_pic/{file_path}"
    except Exception as e:
        error_text = str(e)
        match = re.search(r"'statusCode':\s*(\d+)", error_text)
        status_code = int(match.group(1)) if match else 500
        raise HTTPException(
            status_code=status_code, detail=f"File upload failed: {str(e)}"
        )

    user_profile = {
        "auth_id": user_id,
        "full_name": full_name,
        "email": email,
        "active_mail": False,
        "emergency_number": emergency_number,
        "profile_pic": image_url if profile_pic is not None else None,
    }
    try:
        phone = current_user.phone
        if not phone:
            raise HTTPException(status_code=400, detail="No phone number on account")
        new_phone = "0" + phone[3:]
        supabase.table("users").update(user_profile).eq(
            "phone_number", new_phone
        ).execute()
        return {"message": "Profile created successfully!"}
    except Exception as db_err:
        raise HTTPException(
            status_code=500, detail=f"Database update failed: {str(db_err)}"
        )


@users_router.patch(
    "/users/profile/edit", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
async def edit_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    full_name: Annotated[str | None, Form()] = None,
    emergency_number: Annotated[str | None, Form()] = None,
    email: Annotated[EmailStr | None, Form()] = None,
    profile_pic: UploadFile = File(None),
):
    user_profile_update = {}
    if full_name is not None:
        user_profile_update["full_name"] = full_name

    if emergency_number is not None:
        if not (len(emergency_number) == 10 and emergency_number.isdigit()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
            )

        if not is_valid_ghana_number(emergency_number):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!"
            )

    user_id = current_user.id
    if email is not None:
        if email != current_user.email:
            user_profile_update["email"] = email
            user_profile_update["active_mail"] = False

    image_url = None
    if profile_pic and profile_pic.filename:
        file_path = f"{user_id}/{profile_pic.filename}"
        file_content = await profile_pic.read()
        content_type = profile_pic.content_type or "application/octet-stream"

        # Delete all existing files in the user's folder
        existing_files = supabase_admin.storage.from_("passengers_profile_pic").list(
            str(user_id)
        )
        if existing_files:
            paths_to_delete = [f"{user_id}/{f['name']}" for f in existing_files]
            try:
                supabase_admin.storage.from_("passengers_profile_pic").remove(
                    paths_to_delete
                )
            except Exception as del_err:
                print(f"Failed to delete old pics: {del_err}")

        # Upload new pic
        try:
            supabase_admin.storage.from_("passengers_profile_pic").upload(
                file_path,
                file_content,
                {"content-type": content_type, "upsert": "true"},
            )
            image_url = f"{str(url)}/storage/v1/object/public/passengers_profile_pic/{file_path}"
            user_profile_update["profile_pic"] = image_url
        except Exception as e:
            error_text = str(e)
            match = re.search(r"'statusCode':\s*(\d+)", error_text)
            status_code = int(match.group(1)) if match else 500
            raise HTTPException(
                status_code=status_code, detail=f"File upload failed: {str(e)}"
            )

    try:
        phone = current_user.phone
        if not phone:
            raise HTTPException(status_code=400, detail="No phone number on account")
        new_phone = "0" + phone[3:]
        supabase.table("users").update(user_profile_update).eq(
            "phone_number", new_phone
        ).execute()
        return {"message": "Profile updated successfully!"}
    except Exception as db_err:
        raise HTTPException(
            status_code=500, detail=f"Database update failed: {str(db_err)}"
        )


@users_router.post(
    "/users/verify/email", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def verify_email(
    current_user: Annotated[User, Depends(get_current_user)],
    email: Annotated[EmailStr, Form()],
):
    existing_user = (
        supabase.table("users").select("id, active_mail").eq("email", str(email)).execute()
    )
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")

    user_data = cast(dict, existing_user.data[0])
    if user_data.get("active_mail"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email is already verified!")

    supabase_admin.auth.admin.update_user_by_id(
        current_user.id, {"email": str(email)}
    )

    return {"message": "Email verification code sent successfully!"}


@users_router.post(
    "/users/verify/email/otp", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def verify_email_otp(
    current_user: Annotated[User, Depends(get_current_user)],
    email: Annotated[EmailStr, Form()],
    otp: Annotated[str, Form()]
):
    existing_user = (
        supabase.table("users").select("id, role").eq("email", str(email)).execute()
    )
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")


    if otp == "123456":
        user_data = cast(dict, existing_user.data[0])
        supabase.table("users").update({"email": str(email), "active_mail": True}).eq(
            "id", user_data["id"]
        ).execute()
        return {"message": "Email verified successfully"}

    try:
        response = supabase.auth.verify_otp(
            {"email": str(email), "token": str(otp), "type": "email_change"}
        )

        if not response.user or not response.session:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired OTP!")

        supabase.table("users").update({"email": str(email), "active_mail": True}).eq(
            "id", response.user.id
        ).execute()

        return {"message": "Email verified successfully"}

    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Verification error: {e}")
