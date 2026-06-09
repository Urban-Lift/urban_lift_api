from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel
from datetime import date, time
from typing import Annotated, Any, cast
from enum import Enum
import os
import json
import httpx
from openai import OpenAI
from app.admin_client import supabase_admin as supabase
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
from supabase_auth import User
from app.routes.auth import UserRole
import os
import httpx
from twilio.rest import Client as TwilioClient

ride_router = APIRouter(tags=["Rides"])

base_url = "https://nominatim.openstreetmap.org"
app_header = {"User-Agent": "UrbanLiftAPI/1.0"}

def format_phone_e164(phone: str, country_code: str = "+233") -> str:
    """Convert a local Ghana phone number to E.164 format for Twilio."""
    phone = phone.strip()
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        phone = phone[1:]
    return f"{country_code}{phone}"

class TripType(str, Enum):
    one_way = "one_way"
    round_trip = "round_trip"

class TripStatus(str, Enum):
    scheduled = "scheduled"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"

class RideModel(BaseModel):
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

@ride_router.get(
    "/passenger/ride/track/{booking_id}",
    dependencies=[Depends(has_role(["passenger", "driver"]))],
)
async def track_ride(
    current_user: Annotated[User, Depends(get_current_user)],
    booking_id: int,
):
    booking = (
        supabase.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .execute()
    )
    if not booking.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found"
        )

    booking_data = cast(dict[str, Any], booking.data[0])
    ride = (
        supabase.table("rides").select("*").eq("id", booking_data["ride_id"]).execute()
    )
    if not ride.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found"
        )

    ride_data = cast(dict[str, Any], ride.data[0])

    # Check if user is a driver
    user_profile = (
        supabase.table("users")
        .select("role")
        .eq("auth_id", current_user.id)
        .execute()
    )
    is_driver = (
        user_profile.data
        and cast(dict[str, Any], user_profile.data[0]).get("role") == "driver"
    )

    response: dict[str, Any] = {
        "booking": booking_data,
        "ride": ride_data,
        "status": booking_data.get("status"),
        "driver_lat": ride_data.get("driver_lat"),
        "driver_lng": ride_data.get("driver_lng"),
    }

    # Get route info for both passengers and drivers
    pickup_lat = ride_data.get("pickup_lat")
    pickup_lng = ride_data.get("pickup_lng")
    dropoff_lat = ride_data.get("dropoff_lat")
    dropoff_lng = ride_data.get("dropoff_lng")
    driver_lat = ride_data.get("driver_lat")
    driver_lng = ride_data.get("driver_lng")

    # Calculate remaining distance from driver's current position to dropoff
    origin_lng = driver_lng if driver_lng else pickup_lng
    origin_lat = driver_lat if driver_lat else pickup_lat

    if origin_lat and origin_lng and dropoff_lat and dropoff_lng:
        osrm_url = "https://router.project-osrm.org/route/v1/driving"
        coords = f"{origin_lng},{origin_lat};{dropoff_lng},{dropoff_lat}"
        async with httpx.AsyncClient() as client:
            route_resp = await client.get(
                f"{osrm_url}/{coords}",
                params={"overview": "full", "geometries": "geojson", "steps": "true"},
                headers=app_header,
            )
            if route_resp.status_code == 200:
                route_data = route_resp.json()
                if route_data.get("code") == "Ok" and route_data.get("routes"):
                    route = route_data["routes"][0]
                    total_km = round(route["distance"] / 1000, 2)
                    remaining_min = round(route["duration"] / 60, 2)

                    # Both passengers and drivers get progress info
                    response["progress"] = {
                        "remaining_km": total_km,
                        "remaining_min": remaining_min,
                        "geometry": route.get("geometry"),
                    }

                    # Only drivers get turn-by-turn navigation
                    if is_driver:
                        legs = route.get("legs", [])
                        steps = legs[0].get("steps", []) if legs else []
                        response["navigation"] = {
                            "steps": [
                                {
                                    "instruction": step.get("maneuver", {}).get("type", ""),
                                    "modifier": step.get("maneuver", {}).get("modifier", ""),
                                    "name": step.get("name", ""),
                                    "distance_m": step.get("distance", 0),
                                    "duration_s": step.get("duration", 0),
                                }
                                for step in steps
                            ],
                        }

    return response


@ride_router.post(
    "/passenger/ride/sos/{booking_id}", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def send_sos(
    current_user: Annotated[User, Depends(get_current_user)],
    booking_id: int,
):
    # Verify booking exists and user is either the passenger or the driver
    booking = (
        supabase.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .execute()
    )
    if not booking.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found"
        )

    booking_data = cast(dict[str, Any], booking.data[0])

    # Get the ride to find the driver
    ride = (
        supabase.table("rides")
        .select("driver_id")
        .eq("id", booking_data["ride_id"])
        .execute()
    )
    if not ride.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found"
        )

    ride_data = cast(dict[str, Any], ride.data[0])
    driver_id = ride_data["driver_id"]

    # Ensure the current user is either the passenger or the driver of this ride
    if current_user.id != booking_data["passenger_id"] and current_user.id != driver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this alerting"
        )

    # Get the user's profile to find emergency number
    user_profile = (
        supabase.table("users")
        .select("emergency_number, full_name")
        .eq("auth_id", current_user.id)
        .execute()
    )
    if not user_profile.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found"
        )

    profile_data = cast(dict[str, Any], user_profile.data[0])
    emergency_number = profile_data.get("emergency_number")
    if not emergency_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No emergency number saved in profile",
        )

    # Get the driver's emergency contact
    driver_profile = (
        supabase.table("users")
        .select("emergency_number, full_name")
        .eq("auth_id", driver_id)
        .execute()
    )
    driver_emergency_number = None
    driver_name = "the driver"
    if driver_profile.data:
        driver_profile_data = cast(dict[str, Any], driver_profile.data[0])
        driver_emergency_number = driver_profile_data.get("emergency_number")
        driver_name = driver_profile_data.get("full_name", "the driver")

    # Create SOS record
    sender_name = profile_data.get("full_name", "A user")
    pickup = booking_data.get("pickup_location", "unknown")
    dropoff = booking_data.get("dropoff_location", "unknown")

    sos_data = {
        "booking_id": booking_id,
        "sender_id": booking_data["passenger_id"],
        "emergency_number": emergency_number,
        "ride_id": booking_data["ride_id"],
        "sender_name": sender_name,
        "pickup_location": pickup,
        "dropoff_location": dropoff,
    }
    supabase.table("sos_alerts").insert(sos_data).execute()

    # Send SMS via Twilio
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")

    if not twilio_sid or not twilio_token or not twilio_phone:
        raise HTTPException(status_code=500, detail="SMS service not configured")

    sms_body = (
        f"🚨 SOS ALERT from UrbanLift!\n"
        f"{sender_name} needs help during their ride.\n"
        f"Pickup: {pickup}\n"
        f"Dropoff: {dropoff}\n"
        f"Please check on them immediately."
    )

    sos_recipient = os.getenv("SOS_RECIPIENT_NUMBER")
    formatted_emergency = sos_recipient if sos_recipient else format_phone_e164(emergency_number)
    try:
        twilio_client = TwilioClient(twilio_sid, twilio_token)
        # Send to the sender's emergency contact
        twilio_client.messages.create(
            body=sms_body,
            from_=twilio_phone,
            to=formatted_emergency,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send SOS SMS to {formatted_emergency} (from: {twilio_phone}, sos_env: {sos_recipient}, raw: {emergency_number}): {str(e)}",
        )

    # Also send to the driver's emergency contact (non-fatal if it fails)
    # Skip if SOS_RECIPIENT_NUMBER is set (both would go to the same test number)
    sender_emergency_notified = False
    if not sos_recipient and driver_emergency_number and driver_emergency_number != emergency_number:
        driver_sms = (
            f"🚨 SOS ALERT from UrbanLift!\n"
            f"An SOS was triggered during {driver_name}'s ride.\n"
            f"Pickup: {pickup}\n"
            f"Dropoff: {dropoff}\n"
            f"Please check on them immediately."
        )
        try:
            twilio_client.messages.create(
                body=driver_sms,
                from_=twilio_phone,
                to=format_phone_e164(driver_emergency_number),
            )
            sender_emergency_notified = True
        except Exception:
            pass

    return {
        "message": "SOS alert sent successfully",
        "emergency_number": emergency_number,
        "sender_emergency_notified": sender_emergency_notified,
    }


@ride_router.get(
    "/rides/history", dependencies=[Depends(has_role(["passenger", "driver"]))]
)
def get_ride_history(
    current_user: Annotated[User, Depends(get_current_user)],
):
    user_id = current_user.id

    # Get user's role
    user_profile = (
        supabase.table("users")
        .select("role")
        .eq("auth_id", user_id)
        .execute()
    )
    if not user_profile.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found"
        )

    role = cast(dict[str, Any], user_profile.data[0]).get("role")

    if role == "driver":
        # Get completed rides the driver created
        rides = (
            supabase.table("rides")
            .select("*")
            .eq("driver_id", user_id)
            .eq("trip_status", "completed")
            .execute()
        )
        return {"role": "driver", "completed_rides": rides.data}
    else:
        # Get completed bookings for the passenger
        bookings = (
            supabase.table("bookings")
            .select("*, rides(*)")
            .eq("passenger_id", user_id)
            .eq("status", "completed")
            .execute()
        )
        return {"role": "passenger", "completed_rides": bookings.data}


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
    
@ride_router.post("/ride/match", dependencies=[Depends(has_role(["passenger"]))])
async def match_rides(
    current_user: Annotated[User, Depends(get_current_user)],
    pickup_location: Annotated[str, Form()],
    dropoff_location: Annotated[str, Form()],
    departure_date: Annotated[date, Form()],
    departure_time: Annotated[time, Form()],
    seats_needed: Annotated[int, Form()],
    max_price: Annotated[float | None, Form()] = None,
):
    if departure_date != date.today():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ride matching is only available for today's date.",
        )

    # Fetch available rides
    query = (
        supabase.table("rides")
        .select("*")
        .eq("trip_status", "scheduled")
        .gte("available_seats", seats_needed)
        .eq("departure_date", departure_date.isoformat())
    )
    rides = query.execute()

    if not rides.data:
        return {"matches": [], "explanation": "No available rides found for that date."}

    # Build context for Grok
    rides_json = json.dumps(rides.data, default=str)

    prompt = f"""You are a ride-matching assistant for UrbanLift, a carpooling app in Ghana.

    A passenger is looking for a ride with these preferences:
    - Pickup: {pickup_location}
    - Dropoff: {dropoff_location}
    - Date: {departure_date.isoformat()}
    - Preferred time: {departure_time.isoformat()}
    - Seats needed: {seats_needed}
    - Max price per seat: {max_price if max_price else "no limit"}

    Here are the available rides:
    {rides_json}

    Search for current traffic conditions in the areas around the pickup and dropoff locations (within a reasonable radius). Factor traffic congestion, road closures, and travel time estimates into your ranking.

    Rank the rides from best to worst match considering:
    1. Proximity of pickup/dropoff locations (name similarity and coordinates)
    2. Real-time traffic conditions and estimated travel time in the pickup/dropoff radius
    3. Time closeness to preferred departure time
    4. Price per seat (lower is better, respect max_price if set)
    5. Available seats meeting the requirement

    Return a JSON object with this exact structure:
    {{
    "matches": [
        {{
        "ride_id": <id>,
        "score": <1-100>,
        "reason": "<brief explanation including traffic considerations>"
        }}
    ],
    "explanation": "<brief overall summary including traffic info>"
    }}

    Only include rides with a score of 40 or above. Return at most 5 matches. Return ONLY the JSON, no other text."""

    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="AI matching service not configured")

    xai_client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    completion = xai_client.chat.completions.create(
        model="grok-3-mini",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"search_parameters": {"mode": "auto", "sources": [{"type": "web"}]}},
    )

    # Parse Grok's response
    response_text = completion.choices[0].message.content
    if not response_text:
        raise HTTPException(status_code=500, detail="Empty AI response")
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse AI matching response")

    return result
