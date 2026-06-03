from fastapi import (
    APIRouter, 
    Form, 
    HTTPException, 
    status,
    Depends)
from datetime import date, time
from typing import Annotated
from app.db.config import supabase
from app.admin_client import supabase
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
from supabase_auth import User
from app.routes.rides import TripType

passenger_router = APIRouter(tags=["Rides"])

@passenger_router.get("/passenger/ride/search", dependencies=[Depends(has_role(["passenger"]))])
def find_ride(
    current_user: Annotated[User, Depends(get_current_user)],
    trip_type: TripType,
    pickup_location: str | None = None,
    dropoff_location: str | None = None,
    departure_date: date | None = None,
    departure_time: time | None = None,
    available_seats: int | None = None,
):
    query = supabase.table("rides").select("*")
    if trip_type:
        query = query.eq("trip_type", trip_type.value)
    if pickup_location:
        query = query.ilike("pickup_location", f"%{pickup_location}%")
    if dropoff_location:
        query = query.ilike("dropoff_location", f"%{dropoff_location}%")
    if departure_date:
        query = query.eq("departure_date", departure_date.isoformat())
    if departure_time:
        query = query.eq("departure_time", departure_time.isoformat())
    if available_seats:
        query = query.gte("available_seats", available_seats)
    rides = query.execute()
    return {"rides": rides.data}

@passenger_router.post("/passenger/ride/review", dependencies=[Depends(has_role(["passenger"]))])
def review_trip(
        current_user: Annotated[User, Depends(get_current_user)],
        ride_id: Annotated[int, Form()],
        rating: Annotated[int, Form()],
        comment: Annotated[str | None, Form()] = None,
        note: Annotated[str | None, Form()] = None,
):
    if rating < 1 or rating > 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rating must be between 1 and 5")

    ride = supabase.table("rides").select("*").eq("id", ride_id).execute()
    if not ride.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found")

    review_data = {
        "ride_id": ride_id,
        "passenger_id": current_user.id,
        "rating": rating,
        "comment": comment,
        "note": note,
    }
    supabase.table("reviews").insert(review_data).execute()
    return {"message": "Review submitted successfully"}