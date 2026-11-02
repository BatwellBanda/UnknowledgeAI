from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.routes import auth
from app.routes import documents
from app.routes import users
from app.routes import courses
from app.routes import search
from app.routes import chat
from app.routes import health

app = FastAPI(
    title="UniKnowledge AI",
    description="AI-powered personalized learning and knowledge management platform",
    version="1.0.0"
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(courses.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(health.router)

frontend_directory = Path(__file__).resolve().parents[2] / "Frontend" / "dist"
if frontend_directory.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_directory, html=True), name="student-app")

@app.get("/")
def root():
    return {
        "message": "Welcome to UniKnowledge AI",
        "status": "running"
    }
