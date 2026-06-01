from fastapi import APIRouter, Form, HTTPException, status
from pydantic import BaseModel
from datetime import date, time
from typing import Annotated, cast
from enum import Enum
from app.db.config import supabase
from utils import is_valid_ghana_number
from admin_client import supabase, supabase_admin
from dependecies.authz import has_role
from dependecies.authn import get_current_user

ride_router = APIRouter(tags=["Rides"])

class RideModel(BaseModel):
    role: str
    pickup_location: str
    destination_address: str
    departure_date: date
    departure_time: time
    available_seats: int

@ride_router.post("/ride/search")
def find_ride(
    ride: RideModel
):
    pass