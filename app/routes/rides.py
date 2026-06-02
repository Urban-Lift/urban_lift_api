from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel
from datetime import date, time
from typing import Annotated, cast
from enum import Enum
import httpx
from db.config import supabase
from utils import is_valid_ghana_number
from admin_client import supabase, supabase_admin
from dependecies.authz import has_role
from dependecies.authn import get_current_user
from supabase_auth import User
from routes.auth import UserRole

ride_router = APIRouter(tags=["Rides"])

base_url = "https://nominatim.openstreetmap.org"
app_header = {"User-Agent": "UrbanLiftAPI/1.0"}

class TripType(str, Enum):
    one_way = "one_way"
    round_trip = "round_trip"

class TripStatus(str, Enum):
    scheduled = "scheduled"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"

class RideModel(BaseModel):
    role: UserRole
    pickup_location: str
    dropoff_location: str
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    departure_date: date
    departure_time: time
    est_arrival_time: time
    available_seats: int
    price_per_seat: float
    trip_type: TripType
    trip_status: TripStatus

@ride_router.post("/geocode")
async def geocode_address(
    address: str
):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/search",
            params={"q": address, "format": "jsonv2"},
            headers=app_header,
            )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Reverse geocoding error")
        return {
            "message": "Geocoding successful",
            "lat": response.json()[0]["lat"],
            "lon": response.json()[0]["lon"],
            "display_name": response.json()[0]["display_name"],
        }
    
@ride_router.post("/reverse_geocode")
async def reverse_geocode(
    lat: float,
    lon: float,
    zoom: int = 18,
):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": zoom},
            headers=app_header,
            )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Reverse geocoding error")
        return {
            "message": "Reverse geocoding successful",
            "display_name": response.json().get("display_name")
        }

@ride_router.post("/ride/distance")
async def get_distance(
    origin_lat: Annotated[float, Form()],
    origin_lon: Annotated[float, Form()],
    dest_lat: Annotated[float, Form()],
    dest_lon: Annotated[float, Form()],
):
    osrm_url = "https://router.project-osrm.org/route/v1/driving"
    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{osrm_url}/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            headers=app_header,
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Routing service error")
        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise HTTPException(status_code=400, detail="No route found")
        route = data["routes"][0]
        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": round(route["duration"] / 60, 2),
            "geometry": route.get("geometry"),
        }

@ride_router.post("/ride/create", dependencies=[Depends(has_role(["driver"]))])
def create_ride(
    ride: Annotated[RideModel, Form()],
    current_user: Annotated[User, Depends(get_current_user)],
):
    ride_data = {
        "driver_id": current_user.id,
        "pickup_location": ride.pickup_location,
        "dropoff_location": ride.dropoff_location,
        "departure_date": ride.departure_date.isoformat(),
        "departure_time": ride.departure_time.isoformat(),
        "available_seats": ride.available_seats,
        "price_per_seat": ride.price_per_seat,
        "trip_type": ride.trip_type.value,
        "trip_status": ride.trip_status.value,
        "pickup_lat": ride.pickup_lat,
        "pickup_lng": ride.pickup_lng,
        "dropoff_lat": ride.dropoff_lat,
        "dropoff_lng": ride.dropoff_lng,
    }
    try:
        if ride.role != UserRole.DRIVER:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only drivers can create rides")   
        if ride.price_per_seat <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price per seat must be greater than zero")
        if ride.available_seats <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Available seats must be greater than zero")
        if ride.pickup_lat <= 0 or ride.pickup_lng <= 0 or ride.dropoff_lat <= 0 or ride.dropoff_lng <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid coordinates provided")
        if ride.departure_date < date.today():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Departure date cannot be in the past")
        
        supabase.table("rides").insert(ride_data).execute()
        return {"message": "Ride created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create ride: {str(e)}")
    
@ride_router.get("/ride/search", dependencies=[Depends(has_role(["passenger"]))])
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

@ride_router.post("/ride/review", dependencies=[Depends(has_role(["passenger"]))])
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