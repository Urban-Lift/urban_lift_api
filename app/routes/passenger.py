from fastapi import (
    APIRouter, 
    Form, 
    HTTPException, 
    status, 
    Depends
)
from datetime import date, time
from typing import Annotated, Any, cast
from enum import Enum
import os
import httpx
from twilio.rest import Client as TwilioClient
from app.admin_client import supabase_admin as supabase
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
from supabase_auth import User
from app.routes.rides import TripType

passenger_router = APIRouter(tags=["Passengers"])

class RideStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
class PaymentMethods(str, Enum):
    mobile_money = "mobile_money"
    cash = "cash"
    wallet = "wallet"

class PaymentMethodType(str, Enum):
    mobile_money = "mobile_money"
    card = "card"

@passenger_router.get(
    "/passenger/ride/search", dependencies=[Depends(has_role(["passenger"]))]
)
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


@passenger_router.post(
    "/passenger/ride/book", dependencies=[Depends(has_role(["passenger"]))]
)
async def book_ride(
    current_user: Annotated[User, Depends(get_current_user)],
    seats_booked: Annotated[int, Form()],
    ride_id: Annotated[int, Form()],
    payment_method: Annotated[PaymentMethods, Form()],
    pickup_location: Annotated[str, Form()],
    dropoff_location: Annotated[str, Form()],
):
    # Verify ride exists and has enough seats
    ride = supabase.table("rides").select("*").eq("id", ride_id).execute()
    if not ride.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found"
        )

    ride_data = cast(dict[str, Any], ride.data[0])
    if ride_data["available_seats"] < seats_booked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Not enough available seats"
        )

    # Geocode pickup and dropoff locations to get coordinates
    base_url = "https://nominatim.openstreetmap.org"
    app_header = {"User-Agent": "UrbanLiftAPI/1.0"}

    async with httpx.AsyncClient() as client:
        pickup_resp = await client.get(
            f"{base_url}/search",
            params={"q": pickup_location, "format": "jsonv2"},
            headers=app_header,
        )
        if pickup_resp.status_code != 200 or not pickup_resp.json():
            raise HTTPException(
                status_code=400, detail="Could not geocode pickup location"
            )

        dropoff_resp = await client.get(
            f"{base_url}/search",
            params={"q": dropoff_location, "format": "jsonv2"},
            headers=app_header,
        )
        if dropoff_resp.status_code != 200 or not dropoff_resp.json():
            raise HTTPException(
                status_code=400, detail="Could not geocode dropoff location"
            )

    pickup_data = pickup_resp.json()[0]
    dropoff_data = dropoff_resp.json()[0]

    origin_lat = float(pickup_data["lat"])
    origin_lon = float(pickup_data["lon"])
    dest_lat = float(dropoff_data["lat"])
    dest_lon = float(dropoff_data["lon"])

    # Get distance and duration using OSRM
    osrm_url = "https://router.project-osrm.org/route/v1/driving"
    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    async with httpx.AsyncClient() as client:
        route_resp = await client.get(
            f"{osrm_url}/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            headers=app_header,
        )
        if route_resp.status_code != 200:
            raise HTTPException(
                status_code=route_resp.status_code, detail="Routing service error"
            )
        route_data = route_resp.json()
        if route_data.get("code") != "Ok" or not route_data.get("routes"):
            raise HTTPException(status_code=400, detail="No route found")

    route = route_data["routes"][0]
    distance_km = round(route["distance"] / 1000)
    duration_min = round(route["duration"] / 60)

    # Calculate total price based on price per seat
    total_price = round(ride_data["price_per_seat"] * seats_booked, 2)

    # Create booking
    booking_data = {
        "ride_id": ride_id,
        "passenger_id": current_user.id,
        "seats_booked": seats_booked,
        "total_price": total_price,
        "payment_method": payment_method.value,
        "pickup_location": pickup_location,
        "dropoff_location": dropoff_location,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "status": RideStatus.pending.value,
    }
    supabase.table("bookings").insert(booking_data).execute()

    # Update available seats
    new_seats = ride_data["available_seats"] - seats_booked
    supabase.table("rides").update({"available_seats": new_seats}).eq(
        "id", ride_id
    ).execute()

    return {
        "message": "Ride booked successfully",
        "distance_km": distance_km,
        "duration_min": duration_min,
        "total_price": total_price,
    }


@passenger_router.get(
    "/passenger/ride/booking", dependencies=[Depends(has_role(["passenger"]))]
)
def get_my_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
):
    bookings = (
        supabase.table("bookings")
        .select("*")
        .eq("passenger_id", current_user.id)
        .execute()
    )
    if not bookings.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No bookings found"
        )

    return {"bookings": bookings.data}

@passenger_router.post(
    "/passenger/ride/review", dependencies=[Depends(has_role(["passenger"]))]
)
def review_trip(
    current_user: Annotated[User, Depends(get_current_user)],
    ride_id: Annotated[int, Form()],
    rating: Annotated[int, Form()],
    comment: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
):
    if rating < 1 or rating > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rating must be between 1 and 5",
        )

    ride = supabase.table("rides").select("*").eq("id", ride_id).execute()
    if not ride.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found"
        )
    
    print(ride)

    review_data = {
        "ride_id": ride_id,
        "passenger_id": current_user.id,
        "rating": rating,
        "comment": comment,
        "note": note,
    }
    supabase.table("reviews").insert(review_data).execute()
    return {"message": "Review submitted successfully"}


@passenger_router.post(
    "/passenger/wallet/topup", dependencies=[Depends(has_role(["passenger"]))]
)
def topup_wallet(
    current_user: Annotated[User, Depends(get_current_user)],
    amount: Annotated[float, Form()],
):
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be greater than 0",
        )

    user_id = current_user.id

    # Get current wallet balance
    wallet = (
        supabase.table("wallets")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )

    if not wallet.data:
        # Create wallet with initial balance
        supabase.table("wallets").insert({
            "user_id": user_id,
            "balance": amount,
        }).execute()
        new_balance = amount
    else:
        wallet_data = cast(dict[str, Any], wallet.data[0])
        new_balance = round(wallet_data["balance"] + amount, 2)
        supabase.table("wallets").update({"balance": new_balance}).eq(
            "user_id", user_id
        ).execute()

    # Record the transaction
    supabase.table("wallet_transactions").insert({
        "user_id": user_id,
        "amount": amount,
        "transaction_type": "topup",
    }).execute()

    return {
        "message": "Wallet topped up successfully",
        "amount": amount,
        "new_balance": new_balance,
    }


@passenger_router.get(
    "/passenger/wallet/balance", dependencies=[Depends(has_role(["passenger"]))]
)
def get_wallet_balance(
    current_user: Annotated[User, Depends(get_current_user)],
):
    wallet = (
        supabase.table("wallets")
        .select("balance")
        .eq("user_id", current_user.id)
        .execute()
    )
    if not wallet.data:
        return {"balance": 0.0}

    return {"balance": cast(dict[str, Any], wallet.data[0])["balance"]}


@passenger_router.post(
    "/passenger/payment-methods", dependencies=[Depends(has_role(["passenger"]))]
)
def add_payment_method(
    current_user: Annotated[User, Depends(get_current_user)],
    method_type: Annotated[PaymentMethodType, Form()],
    provider: Annotated[str, Form()],
    account_number: Annotated[str, Form()],
    account_name: Annotated[str | None, Form()] = None,
):
    user_id = current_user.id

    # Check if this payment method already exists
    existing = (
        supabase.table("payment_methods")
        .select("*")
        .eq("user_id", user_id)
        .eq("method_type", method_type.value)
        .eq("account_number", account_number)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This payment method already exists",
        )

    payment_method_data = {
        "user_id": user_id,
        "method_type": method_type.value,
        "provider": provider,
        "account_number": account_number,
        "account_name": account_name,
    }
    supabase.table("payment_methods").insert(payment_method_data).execute()

    return {"message": "Payment method added successfully"}


@passenger_router.get(
    "/passenger/payment-methods", dependencies=[Depends(has_role(["passenger"]))]
)
def get_payment_methods(
    current_user: Annotated[User, Depends(get_current_user)],
):
    methods = (
        supabase.table("payment_methods")
        .select("*")
        .eq("user_id", current_user.id)
        .execute()
    )
    return {"payment_methods": methods.data}


@passenger_router.delete(
    "/passenger/payment-methods/{method_id}", dependencies=[Depends(has_role(["passenger"]))]
)
def delete_payment_method(
    current_user: Annotated[User, Depends(get_current_user)],
    method_id: int,
):
    # Verify ownership
    method = (
        supabase.table("payment_methods")
        .select("*")
        .eq("id", method_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not method.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found",
        )

    supabase.table("payment_methods").delete().eq("id", method_id).execute()
    return {"message": "Payment method deleted successfully"}


@passenger_router.post(
    "/passenger/saved-routes", dependencies=[Depends(has_role(["passenger"]))]
)
def add_saved_route(
    current_user: Annotated[User, Depends(get_current_user)],
    route_name: Annotated[str, Form()],
    pickup_location: Annotated[str, Form()],
    dropoff_location: Annotated[str, Form()],
    pickup_lat: Annotated[float, Form()],
    pickup_lng: Annotated[float, Form()],
    dropoff_lat: Annotated[float, Form()],
    dropoff_lng: Annotated[float, Form()],
):
    route_data = {
        "user_id": current_user.id,
        "route_name": route_name,
        "pickup_location": pickup_location,
        "dropoff_location": dropoff_location,
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
    }
    supabase.table("saved_routes").insert(route_data).execute()
    return {"message": "Route saved successfully"}


@passenger_router.get(
    "/passenger/saved-routes", dependencies=[Depends(has_role(["passenger"]))]
)
def get_saved_routes(
    current_user: Annotated[User, Depends(get_current_user)],
):
    routes = (
        supabase.table("saved_routes")
        .select("*")
        .eq("user_id", current_user.id)
        .execute()
    )
    return {"saved_routes": routes.data}


@passenger_router.patch(
    "/passenger/saved-routes/{route_id}", dependencies=[Depends(has_role(["passenger"]))]
)
def update_saved_route(
    current_user: Annotated[User, Depends(get_current_user)],
    route_id: int,
    route_name: Annotated[str | None, Form()] = None,
    pickup_location: Annotated[str | None, Form()] = None,
    dropoff_location: Annotated[str | None, Form()] = None,
    pickup_lat: Annotated[float | None, Form()] = None,
    pickup_lng: Annotated[float | None, Form()] = None,
    dropoff_lat: Annotated[float | None, Form()] = None,
    dropoff_lng: Annotated[float | None, Form()] = None,
):
    # Verify ownership
    route = (
        supabase.table("saved_routes")
        .select("*")
        .eq("id", route_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not route.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved route not found",
        )

    update_data = {}
    if route_name is not None:
        update_data["route_name"] = route_name
    if pickup_location is not None:
        update_data["pickup_location"] = pickup_location
    if dropoff_location is not None:
        update_data["dropoff_location"] = dropoff_location
    if pickup_lat is not None:
        update_data["pickup_lat"] = pickup_lat
    if pickup_lng is not None:
        update_data["pickup_lng"] = pickup_lng
    if dropoff_lat is not None:
        update_data["dropoff_lat"] = dropoff_lat
    if dropoff_lng is not None:
        update_data["dropoff_lng"] = dropoff_lng

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    supabase.table("saved_routes").update(update_data).eq("id", route_id).execute()
    return {"message": "Route updated successfully"}


@passenger_router.delete(
    "/passenger/saved-routes/{route_id}", dependencies=[Depends(has_role(["passenger"]))]
)
def delete_saved_route(
    current_user: Annotated[User, Depends(get_current_user)],
    route_id: int,
):
    # Verify ownership
    route = (
        supabase.table("saved_routes")
        .select("*")
        .eq("id", route_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not route.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved route not found",
        )

    supabase.table("saved_routes").delete().eq("id", route_id).execute()
    return {"message": "Route deleted successfully"}


