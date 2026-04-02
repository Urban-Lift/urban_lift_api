from fastapi import APIRouter, Form
from fastapi import HTTPException, status, Depends
from typing import Annotated
from config import supabase
from utils import is_valid_ghana_number, replace_user_id
from dependecies.authz import has_role
from dependecies.authn import get_current_user


admin_router = APIRouter(tags=["Admin"])


@admin_router.delete("/admin/users/{user_id}", dependencies=[Depends(has_role(["admin"]))])
def delete_user(
        user_id: int,
        phone_number: Annotated[str, Form()],
        admin_id: Annotated[str, Depends(get_current_user)]):
    existing_user = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")

    supabase.table("users").delete().eq("id", user_id).execute()

    return {
        "message": "User deleted successfully"
    }

@admin_router.get("/admin/users", dependencies=[Depends(has_role(["admin"]))])
def get_users(
    query: str="",
    limit: int=10,
    skip: int=0,
    role: str=""
):
    users = supabase.table("users").select("*")
    if query:
        users = users.ilike("name", f"%{query}%") 

    if role:
        users = users.eq("role", role)

    users = users.range(skip, skip + limit - 1).execute()

    return {
        "users": users.data
    }

