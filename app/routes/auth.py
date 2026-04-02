from fastapi import APIRouter, Form
from fastapi import HTTPException, status
from typing import Annotated, cast
from enum import Enum
from config import supabase
from utils import is_valid_ghana_number
from admin_client import supabase, supabase_admin


users_router = APIRouter(tags=["Users"])

class UserRole(str, Enum):
    PASSENGER = "passenger"
    DRIVER = "driver"
    ADMIN = "admin"

@users_router.post("/users/signup")
def register_user(
        phone_number: Annotated[str, Form()],
        role: Annotated[UserRole, Form()] = UserRole.PASSENGER):
    existing_user = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    if existing_user.data:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists!")
    
    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!")
    
    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

    user_created = {
        "phone_number": phone_number,
        "role": role.value,
        "is_active": False
    }

    supabase.table("users").insert(user_created).execute()

    return {
        "message": "User registered successfully"
    }


@users_router.post("/users/verify/phone_number")
def verify_phone_number(
    phone_number: Annotated[str, Form()]
):
    existing_user = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")
    
    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!")
    
    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")
    
    supabase.table("users").select("id, is_active").eq("phone_number", phone_number).eq("is_active", False).execute()
    formatted = "+233" + phone_number[1:]
    
    supabase.auth.sign_in_with_otp({"phone": formatted})

    return {
        "message": "OTP sent successful!"
    }

@users_router.post("/users/verify/otp")
def verify_otp(
    phone_number: Annotated[str, Form()],
    otp: Annotated[str, Form()]
):
    existing_user = supabase.table("users").select("id, role").eq("phone_number", phone_number).execute()
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")
    
    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!")
    
    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")
    
    formatted = "+233" + phone_number[1:]
    
    response = supabase.auth.verify_otp({
        "phone": formatted,
        "token": otp,
        "type": "sms"
    })
    
    assert response.user is not None
    user_data = cast(dict, existing_user.data[0])  
    user_role = user_data.get("role")
    supabase_admin.auth.admin.update_user_by_id(
        response.user.id,
        {"user_metadata": {"role": user_role}}
    )
    
    if not response.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired OTP!")
    assert response.session is not None
    session = response.session
    supabase.table("users").update({"is_active": True}).eq("phone_number", phone_number).execute()
    return {
        "message": "Phone verified successfully",
        "access_token": session.access_token
    }