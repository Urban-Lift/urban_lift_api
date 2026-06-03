from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase_auth import User
from app.db.config import supabase
import re


def get_current_user(
    authorization: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]
) -> User:
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
        error_text = str(e)
        match = re.search(r"'statusCode':\s*(\d+)", error_text)
        status_code = int(match.group(1)) if match else 500
        
        raise HTTPException(
            status_code=status_code, 
            detail=f"Authentication error: {str(e)}"
        )