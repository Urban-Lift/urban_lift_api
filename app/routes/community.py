from fastapi import APIRouter, Depends, Form, HTTPException, status
from typing import Annotated, Any, cast
from supabase_auth import User
from app.admin_client import supabase_admin as supabase
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user

community_router = APIRouter(tags=["Community"])


# --- Community Groups ---

@community_router.post(
    "/community/groups", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def create_group(
    current_user: Annotated[User, Depends(get_current_user)],
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
):
    group_data = {
        "name": name,
        "description": description,
        "created_by": current_user.id,
    }
    result = supabase.table("community_groups").insert(group_data).execute()
    group = cast(dict[str, Any], result.data[0]) if result.data else None
    if not group:
        raise HTTPException(status_code=500, detail="Failed to create group")

    # Add creator as a member with admin role
    supabase.table("group_members").insert({
        "group_id": group["id"],
        "user_id": current_user.id,
        "role": "admin",
    }).execute()

    return {"message": "Group created successfully", "group": group}


@community_router.get(
    "/community/groups", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def get_groups(
    current_user: Annotated[User, Depends(get_current_user)],
):
    # Get groups the user is a member of
    memberships = (
        supabase.table("group_members")
        .select("group_id")
        .eq("user_id", current_user.id)
        .execute()
    )
    if not memberships.data:
        return {"groups": []}

    group_ids = [cast(dict[str, Any], m)["group_id"] for m in memberships.data]
    groups = (
        supabase.table("community_groups")
        .select("*")
        .in_("id", group_ids)
        .execute()
    )
    return {"groups": groups.data}


@community_router.get(
    "/community/groups/{group_id}", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def get_group(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
):
    group = (
        supabase.table("community_groups")
        .select("*")
        .eq("id", group_id)
        .execute()
    )
    if not group.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Get members
    members = (
        supabase.table("group_members")
        .select("*, users(full_name, profile_pic)")
        .eq("group_id", group_id)
        .execute()
    )

    return {"group": group.data[0], "members": members.data}


@community_router.patch(
    "/community/groups/{group_id}", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def update_group(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
    name: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
):
    # Verify user is the group admin
    membership = (
        supabase.table("group_members")
        .select("role")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .eq("role", "admin")
        .execute()
    )
    if not membership.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only group admins can update the group",
        )

    update_data = {}
    if name is not None:
        update_data["name"] = name
    if description is not None:
        update_data["description"] = description

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )

    supabase.table("community_groups").update(update_data).eq("id", group_id).execute()
    return {"message": "Group updated successfully"}


@community_router.delete(
    "/community/groups/{group_id}", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def delete_group(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
):
    # Verify user is the group admin
    membership = (
        supabase.table("group_members")
        .select("role")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .eq("role", "admin")
        .execute()
    )
    if not membership.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only group admins can delete the group",
        )

    # Delete messages, members, then the group
    supabase.table("group_messages").delete().eq("group_id", group_id).execute()
    supabase.table("group_members").delete().eq("group_id", group_id).execute()
    supabase.table("community_groups").delete().eq("id", group_id).execute()
    return {"message": "Group deleted successfully"}


# --- Group Membership ---

@community_router.post(
    "/community/groups/{group_id}/join", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def join_group(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
):
    # Check group exists
    group = (
        supabase.table("community_groups")
        .select("id")
        .eq("id", group_id)
        .execute()
    )
    if not group.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Check if already a member
    existing = (
        supabase.table("group_members")
        .select("id")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already a member of this group"
        )

    supabase.table("group_members").insert({
        "group_id": group_id,
        "user_id": current_user.id,
        "role": "member",
    }).execute()
    return {"message": "Joined group successfully"}


@community_router.post(
    "/community/groups/{group_id}/leave", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def leave_group(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
):
    membership = (
        supabase.table("group_members")
        .select("*")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not membership.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not a member of this group"
        )

    supabase.table("group_members").delete().eq("group_id", group_id).eq(
        "user_id", current_user.id
    ).execute()
    return {"message": "Left group successfully"}


# --- Group Chat ---

@community_router.post(
    "/community/groups/{group_id}/messages", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def send_message(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
    message: Annotated[str, Form()],
):
    # Verify user is a member
    membership = (
        supabase.table("group_members")
        .select("id")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not membership.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member to send messages",
        )

    msg_data = {
        "group_id": group_id,
        "user_id": current_user.id,
        "message": message,
    }
    supabase.table("group_messages").insert(msg_data).execute()
    return {"message": "Message sent successfully"}


@community_router.get(
    "/community/groups/{group_id}/messages", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def get_messages(
    current_user: Annotated[User, Depends(get_current_user)],
    group_id: int,
    limit: int = 50,
    offset: int = 0,
):
    # Verify user is a member
    membership = (
        supabase.table("group_members")
        .select("id")
        .eq("group_id", group_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not membership.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member to view messages",
        )

    messages = (
        supabase.table("group_messages")
        .select("*, users(full_name, profile_pic)")
        .eq("group_id", group_id)
        .order("created_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {"messages": messages.data}
