from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    status,
    UploadFile,
    File,
    Depends,
)
from typing import Annotated, Any, List, cast
from supabase_auth import User
from app.admin_client import supabase_admin as supabase, supabase_admin
from pydantic import EmailStr
from app.utils import is_valid_ghana_number
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
import os
import re
import httpx
from app.routes.rides import RideModel
from app.routes.auth import UserRole
from datetime import date, time, timedelta, datetime
from enum import Enum

drivers_router = APIRouter(tags=["Drivers"])

class BookingStatus(str, Enum):
    accepted = "accepted"
    ignored = "ignored"


url = os.getenv("SUPABASE_URL")
bucket = os.getenv("SUPABASE_BUCKET")
anon_key = os.getenv("SUPABASE_ANON_KEY")


@drivers_router.patch("/drivers/ride/update/{ride_id}", dependencies=[Depends(has_role(["driver"]))])
def update_ride(
    ride_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    pickup_location: Annotated[str | None, Form()] = None,
    dropoff_location: Annotated[str | None, Form()] = None,
    pickup_lat: Annotated[float | None, Form()] = None,
    pickup_lng: Annotated[float | None, Form()] = None,
    dropoff_lat: Annotated[float | None, Form()] = None,
    dropoff_lng: Annotated[float | None, Form()] = None,
    departure_date: Annotated[date | None, Form()] = None,
    departure_time: Annotated[str | None, Form()] = None,
    est_arrival_time: Annotated[str | None, Form()] = None,
    available_seats: Annotated[int | None, Form()] = None,
    price_per_seat: Annotated[float | None, Form()] = None,
    trip_type: Annotated[str | None, Form()] = None,
    trip_status: Annotated[str | None, Form()] = None,
):
    driver_id = current_user.id

    driver = supabase.table("users").select("*").eq("auth_id", driver_id).execute()
    if not driver.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found!")

    existing_ride = (
        supabase.table("rides")
        .select("*")
        .eq("id", ride_id)
        .eq("driver_id", driver_id)
        .execute()
    )
    if not existing_ride.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found or you don't own this ride!")

    ride_data = {}
    if pickup_location is not None:
        ride_data["pickup_location"] = pickup_location
    if dropoff_location is not None:
        ride_data["dropoff_location"] = dropoff_location
    if pickup_lat is not None:
        ride_data["pickup_lat"] = pickup_lat
    if pickup_lng is not None:
        ride_data["pickup_lng"] = pickup_lng
    if dropoff_lat is not None:
        ride_data["dropoff_lat"] = dropoff_lat
    if dropoff_lng is not None:
        ride_data["dropoff_lng"] = dropoff_lng
    if departure_date is not None:
        if departure_date < date.today():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Departure date cannot be in the past")
        ride_data["departure_date"] = departure_date.isoformat()
    if departure_time is not None:
        ride_data["departure_time"] = departure_time
    if est_arrival_time is not None:
        ride_data["est_arrival_time"] = est_arrival_time
    if available_seats is not None:
        if available_seats <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Available seats must be greater than zero")
        ride_data["available_seats"] = available_seats
    if price_per_seat is not None:
        if price_per_seat <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Price per seat must be greater than zero")
        ride_data["price_per_seat"] = price_per_seat
    if trip_type is not None:
        ride_data["trip_type"] = trip_type
    if trip_status is not None:
        ride_data["trip_status"] = trip_status

    if not ride_data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update!")

    try:
        supabase.table("rides").update(ride_data).eq("id", ride_id).eq("driver_id", driver_id).execute()

        # When trip is marked as completed, update all accepted bookings to completed
        # so the earnings are reflected in the driver's dashboard
        if trip_status == "completed":
            # Check if earnings already recorded for this ride
            existing_earning = (
                supabase.table("driver_earnings")
                .select("id, earned_at")
                .eq("driver_id", driver_id)
                .eq("ride_id", ride_id)
                .execute()
            )

            # Update accepted bookings to completed
            supabase.table("bookings").update(
                {"status": "completed", "payment_made": True}
            ).eq("ride_id", ride_id).eq("status", "accepted").execute()

            # Get all bookings for this ride (accepted or already completed)
            completed_bookings = (
                supabase.table("bookings")
                .select("id, passenger_id, pickup_location, dropoff_location, total_price")
                .eq("ride_id", ride_id)
                .eq("status", "completed")
                .execute()
            )

            bookings_list = cast(list[dict[str, Any]], completed_bookings.data or [])
            ride_total = sum(float(b.get("total_price", 0) or 0) for b in bookings_list)

            if existing_earning.data:
                # Earnings record exists — fix null earned_at if needed
                record = cast(dict[str, Any], existing_earning.data[0])
                if not record.get("earned_at"):
                    supabase.table("driver_earnings").update({
                        "earned_at": datetime.now().isoformat(),
                    }).eq("id", record["id"]).execute()
            elif ride_total > 0:
                # No earnings record yet — insert one
                supabase.table("driver_earnings").insert({
                    "driver_id": driver_id,
                    "ride_id": ride_id,
                    "amount": round(ride_total, 2),
                    "earned_at": datetime.now().isoformat(),
                }).execute()

            # Notify passengers to leave a review
            for booking in bookings_list:
                supabase.table("notifications").insert({
                    "user_id": booking["passenger_id"],
                    "type": "review_request",
                    "title": "Trip Completed - Leave a Review",
                    "body": f"Your trip from {booking['pickup_location']} to {booking['dropoff_location']} is complete. Please leave a review for your driver!",
                    "is_read": False,
                }).execute()

        return {"message": "Ride updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update ride: {str(e)}")


@drivers_router.delete("/drivers/ride/delete/{ride_id}", dependencies=[Depends(has_role(["driver"]))])
def delete_ride(
    ride_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id

    existing_ride = (
        supabase.table("rides")
        .select("id")
        .eq("id", ride_id)
        .eq("driver_id", driver_id)
        .execute()
    )
    if not existing_ride.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found or you don't own this ride!")

    try:
        supabase.table("rides").delete().eq("id", ride_id).eq("driver_id", driver_id).execute()
        return {"message": "Ride deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete ride: {str(e)}")


@drivers_router.get("/drivers/rides", dependencies=[Depends(has_role(["driver", "passenger"]))])
def get_all_rides(
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id

    rides = supabase.table("rides").select("*").eq("driver_id", driver_id).execute()

    return {"rides": rides.data}


@drivers_router.get("/drivers/rides/{ride_id}", dependencies=[Depends(has_role(["driver", "passenger"]))])
def get_ride(
    ride_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id

    ride = (
        supabase.table("rides")
        .select("*")
        .eq("id", ride_id)
        .eq("driver_id", driver_id)
        .execute()
    )
    if not ride.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found!")

    return {"ride": ride.data[0]}


@drivers_router.post("/drivers/ride/create", dependencies=[Depends(has_role(["driver"]))])
async def create_ride(
    ride: Annotated[RideModel, Form()],
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id
   
    driver = supabase.table("users").select("*").eq("auth_id", driver_id).execute()
    driver_registered = supabase.table("driver_car_registration").select("*").eq("approved", True).execute()
    if not driver_registered.data:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not registered yet!")

    if not driver.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found!")

    # Check if driver already has an active ride
    active_rides = (
        supabase.table("rides")
        .select("id")
        .eq("driver_id", driver_id)
        .in_("trip_status", ["active"])
        .execute()
    )
    if active_rides.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active ride. Complete it before creating a new one.",
        )

    if ride.price_per_seat <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price per seat must be greater than zero")
    if ride.available_seats <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Available seats must be greater than zero")
    if not (-90 <= ride.pickup_lat <= 90) or not (-180 <= ride.pickup_lng <= 180) or not (-90 <= ride.dropoff_lat <= 90) or not (-180 <= ride.dropoff_lng <= 180):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid coordinates provided")
    if ride.departure_date < date.today():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Departure date cannot be in the past")

    # Calculate distance and duration using OSRM
    osrm_url = "https://router.project-osrm.org/route/v1/driving"
    coords = f"{ride.pickup_lng},{ride.pickup_lat};{ride.dropoff_lng},{ride.dropoff_lat}"
    async with httpx.AsyncClient() as client:
        route_resp = await client.get(
            f"{osrm_url}/{coords}",
            params={"overview": "false"},
        )
        if route_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Routing service unavailable")
        route_data = route_resp.json()
        if route_data.get("code") != "Ok" or not route_data.get("routes"):
            raise HTTPException(status_code=400, detail="No route found between locations")

    route = route_data["routes"][0]
    distance_km = round(route["distance"] / 1000, 1)
    duration_min = round(route["duration"] / 60)

    # Calculate estimated arrival time from departure_time + duration
    departure_dt = datetime.combine(ride.departure_date, ride.departure_time)
    arrival_dt = departure_dt + timedelta(minutes=duration_min)
    est_arrival_time = arrival_dt.time()

    ride_data = {
        "driver_id": driver_id,
        "pickup_location": ride.pickup_location,
        "dropoff_location": ride.dropoff_location,
        "departure_date": ride.departure_date.isoformat(),
        "departure_time": ride.departure_time.isoformat(),
        "est_arrival_time": est_arrival_time.isoformat(),
        "available_seats": ride.available_seats,
        "price_per_seat": ride.price_per_seat,
        "trip_type": ride.trip_type.value,
        "trip_status": ride.trip_status.value,
        "pickup_lat": ride.pickup_lat,
        "pickup_lng": ride.pickup_lng,
        "dropoff_lat": ride.dropoff_lat,
        "dropoff_lng": ride.dropoff_lng,
        "distance_km": distance_km,
        "duration_min": duration_min,
    }

    supabase.table("rides").insert(ride_data).execute()
    return {
        "message": "Ride created successfully",
        "distance_km": distance_km,
        "duration_min": duration_min,
        "est_arrival_time": est_arrival_time.isoformat(),
    }


@drivers_router.post(
    "/drivers/registration/create", dependencies=[Depends(has_role(["driver"]))]
)
async def driver_car_registration(
    current_user: Annotated[User, Depends(get_current_user)],
    full_name: Annotated[str, Form()],
    phone_number: Annotated[str, Form()],
    
    ghana_card: Annotated[str, Form()],
    license_plate_num: Annotated[str, Form()],
    car_model: Annotated[str, Form()],
    car_color: Annotated[str, Form()],
    car_year: Annotated[int, Form()],
    # card_image: Annotated[List[UploadFile], File()],
    card_image: Annotated[UploadFile, File()],
    driver_license: Annotated[UploadFile, File()],
    vehicle_insurance: Annotated[UploadFile, File()],
    # car_pic: Annotated[List[UploadFile], File()],
    car_pic: Annotated[UploadFile, File()],
    email: Annotated[EmailStr | None, Form()] = None,
):
    existing_driver_data = (
            supabase.table("driver_car_registration")
            .select("ghana_card, license_plate_num, email, phone_number")
            .or_(
                f"ghana_card.eq.{ghana_card},"
                f"license_plate_num.eq.{license_plate_num},"
                f"email.eq.{email},"
                f"phone_number.eq.{phone_number}"
            )
            .execute()
        )
    if existing_driver_data.data:
        raise HTTPException(status.HTTP_409_CONFLICT, "Driver with the same Ghana card, license plate number, email, or phone number already exists.")
    existing_driver = supabase.table("driver_car_registration").select("auth_id").eq("auth_id", current_user.id).execute()
    if existing_driver.data:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already registered as a driver.")
    
    if not (len(phone_number) == 10 and phone_number.isdigit()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone number!"
        )

    if not is_valid_ghana_number(phone_number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Ghana phone number!")
    
    if len(ghana_card) != 15 or not re.match(r"GHA-[0-9]{9}-[0-9]{1}", ghana_card):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid Ghana card number format! Expected format: GHA-XXXXXXXXX-X")
    
    if car_year < 1980 or car_year > date.today().year:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid car year! Year must be between 1980 and the current year.")
    
    # if len(card_image) <= 1 and len(car_pic) <= 1:
    #     raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "At least 2 card images are required!")

    user_id = current_user.id
    # 0239237162
    card_urls = []
    license_url = None
    insurance_url = None
    car_pic_urls = []
    try:
        # for card in card_image:
        if card_image and card_image.filename:
            file_path = f"{user_id}/{card_image.filename}"
            file_content = await card_image.read()
            content_type = card_image.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            card_urls = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"
            # card_urls.append(f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}")

        if driver_license and driver_license.filename:
            file_path = f"{user_id}/{driver_license.filename}"
            file_content = await driver_license.read()
            content_type = driver_license.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            license_url = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"

        if vehicle_insurance and vehicle_insurance.filename:
            file_path = f"{user_id}/{vehicle_insurance.filename}"
            file_content = await vehicle_insurance.read()
            content_type = vehicle_insurance.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            insurance_url = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"

        # for pic in car_pic:
            if car_pic and car_pic.filename:
                file_path = f"{user_id}/{car_pic.filename}"
                file_content = await car_pic.read()
                content_type = car_pic.content_type or "application/octet-stream"

            supabase_admin.storage.from_("driver_car_documents").upload(
                file_path, file_content, {"content-type": content_type}
            )

            car_pic_urls = f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}"
            # car_pic_urls.append(f"{str(url)}/storage/v1/object/public/driver_car_documents/{file_path}")
    except Exception as e:
        error_text = str(e)
        match = re.search(r"'statusCode':\s*(\d+)", error_text)
        status_code = int(match.group(1)) if match else 500
        raise HTTPException(
            status_code=status_code, detail=f"File upload failed: {str(e)}"
        )

    registration_details = {
        "auth_id": user_id,
        "full_name": full_name,
        "email": email,
        "phone_number": phone_number,
        "ghana_card": ghana_card,
        "license_plate_num": license_plate_num,
        "car_model": car_model,
        "car_color": car_color,
        "car_year": car_year,
        "card_image": card_urls,
        # "card_image_urls": card_urls,
        "driver_license": license_url,
        "vehicle_insurance": insurance_url,
        "car_pic": car_pic_urls,
        # "car_pic_urls": car_pic_urls,
        "approved": False
    }
    try:
        
        supabase.table("driver_car_registration").insert(registration_details).execute()
        return {"message": "Driver and car registered successfully!"}
    except Exception as db_err:
        raise HTTPException(
            status_code=500, detail=f"Database update failed: {str(db_err)}"
        )


@drivers_router.get(
    "/drivers/earnings", dependencies=[Depends(has_role(["driver"]))]
)
def get_earnings_dashboard(
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    # Get all earning records from driver_earnings table
    earnings = (
        supabase.table("driver_earnings")
        .select("amount, earned_at, created_at")
        .eq("driver_id", driver_id)
        .execute()
    )

    earnings_data = cast(list[dict[str, Any]], earnings.data or [])

    if not earnings_data:
        return {
            "total_earnings": 0.0,
            "yearly_earnings": 0.0,
            "monthly_earnings": 0.0,
            "weekly_earnings": 0.0,
            "daily_earnings": 0.0,
            "total_rides": 0,
        }

    total_earnings = 0.0
    yearly_earnings = 0.0
    monthly_earnings = 0.0
    weekly_earnings = 0.0
    daily_earnings = 0.0

    for record in earnings_data:
        amount = float(record.get("amount", 0) or 0)
        earned_at_str = record.get("earned_at") or record.get("created_at")
        total_earnings += amount

        if earned_at_str:
            earned_date = datetime.fromisoformat(earned_at_str.replace("Z", "+00:00")).date()
            if earned_date >= year_start:
                yearly_earnings += amount
            if earned_date >= month_start:
                monthly_earnings += amount
            if earned_date >= week_start:
                weekly_earnings += amount
            if earned_date == today:
                daily_earnings += amount

    result = {
        "total_earnings": round(total_earnings, 2),
        "yearly_earnings": round(yearly_earnings, 2),
        "monthly_earnings": round(monthly_earnings, 2),
        "weekly_earnings": round(weekly_earnings, 2),
        "daily_earnings": round(daily_earnings, 2),
        "total_rides": len(earnings_data),
    }

    # Update the driver_earnings_summary table with computed values
    existing_summary = (
        supabase.table("driver_earnings")
        .select("id")
        .eq("driver_id", driver_id)
        .execute()
    )
    summary_data = {
        "driver_id": driver_id,
        "total_earnings": result["total_earnings"],
        "yearly_earnings": result["yearly_earnings"],
        "monthly_earnings": result["monthly_earnings"],
        "weekly_earnings": result["weekly_earnings"],
        "daily_earnings": result["daily_earnings"],
        "total_rides": result["total_rides"]
    }
    if existing_summary.data:
        supabase.table("driver_earnings").update(summary_data).eq("driver_id", driver_id).execute()
    else:
        supabase.table("driver_earnings").insert(summary_data).execute()

    return result


@drivers_router.get(
    "/drivers/transactions", dependencies=[Depends(has_role(["driver"]))]
)
def get_driver_transactions(
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id

    # Get all completed rides for this driver
    completed_rides = (
        supabase.table("rides")
        .select("id, pickup_location, dropoff_location, departure_date, departure_time")
        .eq("driver_id", driver_id)
        .eq("trip_status", "completed")
        .execute()
    )

    rides_data = cast(list[dict[str, Any]], completed_rides.data or [])
    ride_ids = [r["id"] for r in rides_data]

    if not ride_ids:
        return {"transactions": []}

    ride_map = {r["id"]: r for r in rides_data}

    # Get all completed bookings for those rides
    bookings = (
        supabase.table("bookings")
        .select("id, ride_id, passenger_id, seats_booked, total_price, payment_method, pickup_location, dropoff_location, distance_km, duration_min, created_at")
        .in_("ride_id", ride_ids)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .execute()
    )

    bookings_data = cast(list[dict[str, Any]], bookings.data or [])
    if not bookings_data:
        return {"transactions": []}

    # Get passenger names
    passenger_ids = list({b["passenger_id"] for b in bookings_data})
    passengers_data: list[dict[str, Any]] = []
    if passenger_ids:
        passengers = (
            supabase.table("users")
            .select("auth_id, full_name")
            .in_("auth_id", passenger_ids)
            .execute()
        )
        passengers_data = cast(list[dict[str, Any]], passengers.data or [])
    passenger_map = {p["auth_id"]: p["full_name"] for p in passengers_data}

    transactions = []
    for booking in bookings_data:
        ride = ride_map.get(booking["ride_id"], {})
        transactions.append({
            "booking_id": booking["id"],
            "ride_id": booking["ride_id"],
            "passenger_name": passenger_map.get(booking["passenger_id"], "Unknown"),
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


@drivers_router.get(
    "/drivers/notifications", dependencies=[Depends(has_role(["driver"]))]
)
def get_driver_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
):
    driver_id = current_user.id

    notifications = (
        supabase.table("notifications")
        .select("*")
        .eq("user_id", driver_id)
        .order("created_at", desc=True)
        .execute()
    )

    return {"notifications": notifications.data}


@drivers_router.patch(
    "/drivers/notifications/{notification_id}/read", dependencies=[Depends(has_role(["driver"]))]
)
def mark_notification_read(
    current_user: Annotated[User, Depends(get_current_user)],
    notification_id: int,
):
    driver_id = current_user.id

    notification = (
        supabase.table("notifications")
        .select("*")
        .eq("id", notification_id)
        .eq("user_id", driver_id)
        .execute()
    )
    if not notification.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    supabase.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()
    return {"message": "Notification marked as read"}


@drivers_router.get(
    "/drivers/booking-requests", dependencies=[Depends(has_role(["driver"]))]
)
def get_booking_requests(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get all pending booking requests for the driver's rides."""
    driver_id = current_user.id

    # Get driver's rides
    rides = (
        supabase.table("rides")
        .select("id, pickup_location, dropoff_location, departure_date, departure_time")
        .eq("driver_id", driver_id)
        .execute()
    )
    rides_data = cast(list[dict[str, Any]], rides.data or [])
    ride_ids = [r["id"] for r in rides_data]

    if not ride_ids:
        return {"booking_requests": []}

    ride_map = {r["id"]: r for r in rides_data}

    # Get pending and accepted bookings for those rides
    bookings = (
        supabase.table("bookings")
        .select("id, ride_id, passenger_id, seats_booked, total_price, payment_method, pickup_location, dropoff_location, distance_km, duration_min, status, created_at")
        .in_("ride_id", ride_ids)
        .in_("status", ["pending", "accepted"])
        .order("created_at", desc=True)
        .execute()
    )

    bookings_data = cast(list[dict[str, Any]], bookings.data or [])
    if not bookings_data:
        return {"booking_requests": []}

    # Get passenger names
    passenger_ids = list({b["passenger_id"] for b in bookings_data})
    passengers_data: list[dict[str, Any]] = []
    if passenger_ids:
        passengers = (
            supabase.table("users")
            .select("auth_id, full_name, profile_pic")
            .in_("auth_id", passenger_ids)
            .execute()
        )
        passengers_data = cast(list[dict[str, Any]], passengers.data or [])
    passenger_map = {p["auth_id"]: p for p in passengers_data}

    booking_requests = []
    for booking in bookings_data:
        ride = ride_map.get(booking["ride_id"], {})
        passenger = passenger_map.get(booking["passenger_id"], {})
        booking_requests.append({
            "booking_id": booking["id"],
            "ride_id": booking["ride_id"],
            "passenger_name": passenger.get("full_name", "Unknown"),
            "passenger_pic": passenger.get("profile_pic"),
            "pickup_location": booking["pickup_location"],
            "dropoff_location": booking["dropoff_location"],
            "departure_date": ride.get("departure_date"),
            "departure_time": ride.get("departure_time"),
            "seats_booked": booking["seats_booked"],
            "total_price": booking["total_price"],
            "payment_method": booking["payment_method"],
            "distance_km": booking["distance_km"],
            "duration_min": booking["duration_min"],
            "status": booking["status"],
            "requested_at": booking["created_at"],
        })

    return {"booking_requests": booking_requests}


@drivers_router.patch(
    "/drivers/booking-requests/{booking_id}/update", dependencies=[Depends(has_role(["driver"]))]
)
def update_booking_request(
    current_user: Annotated[User, Depends(get_current_user)],
    booking_id: int,
    booking_status: Annotated[BookingStatus, Form()],
):
    """Update the status of a pending booking request."""
    driver_id = current_user.id
    if booking_status not in BookingStatus:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid booking status. Must be one of: {[status.value for status in BookingStatus]}",
        )

    # Get the booking
    booking = (
        supabase.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .eq("status", "pending")
        .execute()
    )
    booking_data = cast(list[dict[str, Any]], booking.data or [])
    if not booking_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking request not found or already processed",
        )

    booking_record = booking_data[0]
    ride_id = booking_record["ride_id"]

    # Verify the driver owns this ride
    ride = (
        supabase.table("rides")
        .select("id, available_seats, driver_id")
        .eq("id", ride_id)
        .eq("driver_id", driver_id)
        .execute()
    )
    ride_data = cast(list[dict[str, Any]], ride.data or [])
    if not ride_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't own this ride",
        )

    seats_booked = booking_record["seats_booked"]
    available = ride_data[0]["available_seats"]
    if available < seats_booked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough available seats",
        )

    # Accept the booking
    supabase.table("bookings").update({"status": booking_status.value}).eq("id", booking_id).execute()

    # Update available seats
    new_seats = available - seats_booked
    supabase.table("rides").update({"available_seats": new_seats}).eq("id", ride_id).execute()

    # Notify the passenger
    supabase.table("notifications").insert({
        "user_id": booking_record["passenger_id"],
        "type": f"booking_{booking_status.value}",
        "title": f"Booking {booking_status.value.capitalize()}",
        "body": f"Your booking from {booking_record['pickup_location']} to {booking_record['dropoff_location']} has been {booking_status.value}.",
        "is_read": False
    }).execute()

    return {"message": f"Booking request {booking_status}"}
