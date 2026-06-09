from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.auth import users_router
from app.routes.admin import admin_router
from app.routes.rides import ride_router
from app.routes.driver import drivers_router
from app.routes.passenger import passenger_router

app = FastAPI(
    title="A CARPOOLING API",
    description="A basic backend for a carpooling project"
)

origins = [
    "http://localhost:8081",
    "http://localhost:8000",
    "https://urban-lift-api.onrender.com/",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "message": "You are welcome to UrbanLift project"
    }

app.include_router(users_router)
app.include_router(drivers_router)
app.include_router(passenger_router)
app.include_router(ride_router)
app.include_router(admin_router)