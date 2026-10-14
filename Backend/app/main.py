from fastapi import FastAPI
from app.routes import auth
from app.routes import documents
from app.routes import users
from app.routes import courses

app = FastAPI(
    title="UniKnowledge AI",
    description="AI-powered personalized learning and knowledge management platform",
    version="1.0.0"
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(courses.router)

@app.get("/")
def root():
    return {
        "message": "Welcome to UniKnowledge AI",
        "status": "running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }
