from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    status,
    UploadFile,
    File,
    Depends,
)
from typing import Annotated,List
from supabase_auth import User
from app.admin_client import supabase_admin as supabase, supabase_admin
from pydantic import EmailStr
from app.utils import is_valid_ghana_number
from app.dependecies.authz import has_role
from app.dependecies.authn import get_current_user
import os
import re
from app.routes.rides import RideModel
from app.routes.auth import UserRole
from datetime import date

drivers_router = APIRouter(tags=["Drivers"])


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
def create_ride(
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
    ride_data = {
        "driver_id": driver_id,
        "pickup_location": ride.pickup_location,
        "dropoff_location": ride.dropoff_location,
        "departure_date": ride.departure_date.isoformat(),
        "departure_time": ride.departure_time.isoformat(),
        "est_arrival_time": ride.est_arrival_time.isoformat() if ride.est_arrival_time else None,
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
          
        if ride.price_per_seat <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price per seat must be greater than zero")
        if ride.available_seats <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Available seats must be greater than zero")
        if not (-90 <= ride.pickup_lat <= 90) or not (-180 <= ride.pickup_lng <= 180) or not (-90 <= ride.dropoff_lat <= 90) or not (-180 <= ride.dropoff_lng <= 180):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid coordinates provided")
        if ride.departure_date < date.today():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Departure date cannot be in the past")
        
        supabase.table("rides").insert(ride_data).execute()
        return {"message": "Ride created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create ride: {str(e)}")


@drivers_router.post(
    "/drivers/registration/create", dependencies=[Depends(has_role(["driver"]))]
)
async def driver_car_registration(
    current_user: Annotated[User, Depends(get_current_user)],
    full_name: Annotated[str, Form()],
    phone_number: Annotated[str, Form()],
    email: Annotated[EmailStr, Form()],
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
):
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


