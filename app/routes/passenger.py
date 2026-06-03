from fastapi import (
    APIRouter, 
    Form, 
    HTTPException, 
    status, 
    UploadFile, 
    File, 
    Depends)
from pydantic import BaseModel, EmailStr
from datetime import date, time
from typing import Annotated, cast
from enum import Enum
from app.db.config import supabase
from app.utils import is_valid_ghana_number, validate_file
from admin_client import supabase, supabase_admin
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user

passenger_router = APIRouter(tags=["Rides"])




    
    