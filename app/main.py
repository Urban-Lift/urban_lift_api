from fastapi import FastAPI

app = FastAPI(
    title="A CARPOOLING API",
    description="A basic backend for a carpooling project"
)

@app.get("/")
def home():
    return {
        "message": "You are welcome to UrbanLift project"
    }