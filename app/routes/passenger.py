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
import hashlib
from app.utils import is_valid_ghana_number, mtn_numbers, vodafone_numbers, at_numbers

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

class Provider(str, Enum):
    mtn_momo = "mtn_momo"
    vodafone_cash = "vodafone_cash"
    at_money = "at_money"
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

    # Notify the driver about the incoming booking
    supabase.table("notifications").insert({
        "user_id": ride_data["driver_id"],
        "type": "incoming_ride",
        "title": "New Ride Booking",
        "body": f"A passenger booked {seats_booked} seat(s) from {pickup_location} to {dropoff_location}.",
        "is_read": False,
    }).execute()

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
    provider: Annotated[Provider, Form()],
    account_number: Annotated[str, Form()],
    is_default: Annotated[bool, Form()],
    is_active: Annotated[bool, Form()],
):
    user_id = current_user.id
    if provider in [Provider.mtn_momo, Provider.vodafone_cash, Provider.at_money]:
        if not (len(account_number) == 10 and account_number.isdigit()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid momo number!"
            )

        if not is_valid_ghana_number(account_number):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")
        
        if provider == Provider.mtn_momo and account_number[:3] not in mtn_numbers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "MTN number must start with one of: " + ", ".join(mtn_numbers))
        if provider == Provider.vodafone_cash and account_number[:3] not in vodafone_numbers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telecel number must start with one of: " + ", ".join(vodafone_numbers))
        if provider == Provider.at_money and account_number[:3] not in at_numbers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "at number must start with one of: " + ", ".join(at_numbers))
        
    if provider == Provider.card:
        if not (len(account_number) == 16 and account_number.isdigit()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid card number!"
            )

    # Check if this payment method already exists
    salted = f"{os.getenv("JWT_SECRET_KEY")}{account_number}"
    hash_number = hashlib.sha256(salted.encode()).hexdigest()
    existing = (
        supabase.table("payment_methods")
        .select("*")
        .eq("user_id", user_id)
        .eq("provider", provider.value)
        .eq("account_number", hash_number)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This payment method already exists",
        )

    if is_default:
        default = (
            supabase.table("payment_methods")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_default", True)
            .execute()
        )
        if default.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have a default payment method. Remove it before setting a new one.",
            )

    payment_method_data = {
        "user_id": user_id,
        "provider": provider.value,
        "account_number": hash_number,
        "last_four": account_number[-4:],
        "is_default": is_default,
        "is_active": is_active,
    }
    supabase.table("payment_methods").insert(payment_method_data).execute()

    return {"message": "Payment method added successfully"}

@passenger_router.patch(
    "/passenger/payment-methods", dependencies=[Depends(has_role(["passenger"]))]
)
def update_payment_method(
    current_user: Annotated[User, Depends(get_current_user)],
    payment_method_id: int,
    provider: Annotated[Provider | None, Form()] = None,
    account_number: Annotated[str | None, Form()] = None,
    is_default: Annotated[bool | None, Form()] = None,
    is_active: Annotated[bool | None, Form()] = None,
):
    user_id = current_user.id

    # Verify ownership
    method = (
        supabase.table("payment_methods")
        .select("*")
        .eq("payment_method_id", payment_method_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not method.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found",
        )

    update_data: dict[str, Any] = {}

    if account_number is not None and provider is not None:
        if provider in [Provider.mtn_momo, Provider.vodafone_cash, Provider.at_money]:
            if not (len(account_number) == 10 and account_number.isdigit()):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid momo number!"
                )

            if not is_valid_ghana_number(account_number):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")

            if provider == Provider.mtn_momo and account_number[:3] not in mtn_numbers:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "MTN number must start with one of: " + ", ".join(mtn_numbers))
            if provider == Provider.vodafone_cash and account_number[:3] not in vodafone_numbers:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telecel number must start with one of: " + ", ".join(vodafone_numbers))
            if provider == Provider.at_money and account_number[:3] not in at_numbers:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "at number must start with one of: " + ", ".join(at_numbers))

        if provider == Provider.card:
            if not (len(account_number) == 16 and account_number.isdigit()):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid card number!"
                )

        salted = f"{os.getenv("JWT_SECRET_KEY")}{account_number}"
        hash_number = hashlib.sha256(salted.encode()).hexdigest()

        # Check if this payment method already exists
        existing = (
            supabase.table("payment_methods")
            .select("*")
            .eq("user_id", user_id)
            .eq("provider", provider.value)
            .eq("account_number", hash_number)
            .neq("payment_method_id", payment_method_id)
            .execute()
        )
        if existing.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payment method already exists",
            )

        update_data["provider"] = provider.value
        update_data["account_number"] = hash_number
        update_data["last_four"] = account_number[-4:]

    if is_default is not None:
        if is_default:
            default = (
                supabase.table("payment_methods")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_default", True)
                .neq("payment_method_id", payment_method_id)
                .execute()
            )
            if default.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You already have a default payment method. Remove it before setting a new one.",
                )
        update_data["is_default"] = is_default

    if is_active is not None:
        update_data["is_active"] = is_active

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    supabase.table("payment_methods").update(update_data).eq("payment_method_id", payment_method_id).execute()

    return {"message": "Payment method updated successfully"}


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
    payment_method_id: int,
):
    # Verify ownership
    method = (
        supabase.table("payment_methods")
        .select("*")
        .eq("payment_method_id", payment_method_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not method.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found",
        )

    supabase.table("payment_methods").delete().eq("payment_method_id", payment_method_id).execute()
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


@passenger_router.get(
    "/passenger/transactions", dependencies=[Depends(has_role(["passenger"]))]
)
def get_passenger_transactions(
    current_user: Annotated[User, Depends(get_current_user)],
):
    user_id = current_user.id

    # Get all completed bookings for this passenger
    bookings = (
        supabase.table("bookings")
        .select("id, ride_id, seats_booked, total_price, payment_method, pickup_location, dropoff_location, distance_km, duration_min, status, created_at")
        .eq("passenger_id", user_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .execute()
    )

    bookings_data = cast(list[dict[str, Any]], bookings.data or [])
    if not bookings_data:
        return {"transactions": []}

    # Get ride details for driver info
    ride_ids = list({b["ride_id"] for b in bookings_data})
    rides = (
        supabase.table("rides")
        .select("id, driver_id, departure_date, departure_time")
        .in_("id", ride_ids)
        .execute()
    )
    rides_data = cast(list[dict[str, Any]], rides.data or [])
    ride_map = {r["id"]: r for r in rides_data}

    # Get driver names
    driver_ids = list({r["driver_id"] for r in rides_data if r.get("driver_id")})
    drivers_data: list[dict[str, Any]] = []
    if driver_ids:
        drivers = (
            supabase.table("users")
            .select("auth_id, full_name")
            .in_("auth_id", driver_ids)
            .execute()
        )
        drivers_data = cast(list[dict[str, Any]], drivers.data or [])
    driver_map = {d["auth_id"]: d["full_name"] for d in drivers_data}

    transactions = []
    for booking in bookings_data:
        ride = ride_map.get(booking["ride_id"], {})
        transactions.append({
            "booking_id": booking["id"],
            "ride_id": booking["ride_id"],
            "driver_name": driver_map.get(ride.get("driver_id", ""), "Unknown"),
            "pickup_location": booking["pickup_location"],
            "dropoff_location": booking["dropoff_location"],
            "departure_date": ride.get("departure_date"),
            "departure_time": ride.get("departure_time"),
            "seats_booked": booking["seats_booked"],
            "total_price": booking["total_price"],
            "payment_method": booking["payment_method"],
            "distance_km": booking["distance_km"],
            "duration_min": booking["duration_min"],
            "date": booking["created_at"],
        })

    return {"transactions": transactions}


