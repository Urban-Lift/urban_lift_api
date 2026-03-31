from .authn import get_current_user
from fastapi import Depends, HTTPException, status
from typing import Annotated, Any


def has_role(allowed_roles: list[str]):
    def check_role(
           user: Annotated[Any, Depends(get_current_user)]):
        user_role = user.user_metadata.get("role")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Access denied: Insufficient permissions!"
            )
        return user
    return check_role