from fastapi import APIRouter, Form, HTTPException, status
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

ride_router = APIRouter(tags=["Rides"])

base_url = "https://nominatim.openstreetmap.org"
app_header = {"User-Agent": "UrbanLiftAPI/1.0"}

class RideModel(BaseModel):
    role: str
    pickup_location: str
    destination_address: str
    departure_date: date
    departure_time: time
    available_seats: int

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


@ride_router.post("/ride/search")
def find_ride(
    ride: RideModel
):
    pass