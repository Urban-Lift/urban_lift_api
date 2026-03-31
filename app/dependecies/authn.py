import os
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from config import supabase


def get_current_user(
    authorization: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]
):
    token = authorization.credentials
    try:
        response = supabase.auth.get_user(token)
        if not response or not hasattr(response, "user") or response.user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="User session invalid or expired"
            )
        return response.user
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired authentication token!"
        )