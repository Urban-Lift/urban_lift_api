from fastapi import FastAPI
from routes.auth import users_router
from routes.admin import admin_router

app = FastAPI(
    title="A CARPOOLING API",
    description="A basic backend for a carpooling project"
)

@app.get("/")
def home():
    return {
        "message": "You are welcome to UrbanLift project"
    }

app.include_router(users_router)
app.include_router(admin_router)