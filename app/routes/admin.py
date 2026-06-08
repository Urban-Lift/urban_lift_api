from fastapi import APIRouter, Form
from fastapi import HTTPException, status, Depends
from typing import Annotated
from app.db.config import supabase
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user


admin_router = APIRouter(tags=["Admin"])

@admin_router.get(
    "/admin/drivers/registrations/fetch", dependencies=[Depends(has_role(["admin"]))]
)
def get_registrations():

    registration_data = supabase.table("driver_car_registration").select("*").execute()

    if not registration_data.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration_data not found!")

    return {"registration_data": registration_data.data}

@admin_router.patch(
    "/admin/drivers/registration/approve", dependencies=[Depends(has_role(["admin"]))]
)
async def approve_registration(
    approved: Annotated[bool, Form()],
    registration_id: Annotated[int, Form()]
):
    try:
        supabase.table("driver_car_registration").update({"approved": approved}).eq(
            "id", registration_id
        ).execute()
        return {"message": "Registration approved successfully!"}
    except Exception as db_err:
        raise HTTPException(
            status_code=500, detail=f"Database update failed: {str(db_err)}"
        )
    
@admin_router.delete("/admin/users/{user_id}", dependencies=[Depends(has_role(["admin"]))])
def delete_user(
        phone_number: Annotated[str, Form()],
        admin_id: Annotated[str, Depends(get_current_user)]):
    existing_user = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    if not existing_user.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found!")

    supabase.table("users").delete().eq("phone_number", phone_number).execute()

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

