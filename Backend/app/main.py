import logging
import re
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.routes import auth
from app.routes import documents
from app.routes import users
from app.routes import courses
from app.routes import search
from app.routes import chat
from app.routes import health
from app.routes import learning

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
access_log = logging.getLogger("uniknowledge.access")

app = FastAPI(
    title="UniKnowledge AI",
    description="AI-powered personalized learning and knowledge management platform",
    version="1.0.0"
)

# The student app loads only same-origin assets; the API docs pages need their CDN assets.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_and_logging(request: Request, call_next):
    supplied = request.headers.get("x-request-id", "")
    # Accept a caller's ID only in a safe form, so it cannot forge log lines.
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied) else uuid.uuid4().hex
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith(DOCS_PATHS):
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Paths only: query strings and bodies may contain personal data.
    access_log.info("%s %s %s %.0fms id=%s", request.method, request.url.path, response.status_code,
                    (time.perf_counter() - started) * 1000, request_id)
    return response


app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(courses.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(health.router)
for learning_router in (learning.topics, learning.questions, learning.quizzes, learning.plans):
    app.include_router(learning_router)

frontend_directory = Path(__file__).resolve().parents[2] / "Frontend" / "dist"
if frontend_directory.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_directory, html=True), name="student-app")

@app.get("/")
def root():
    return {
        "message": "Welcome to UniKnowledge AI",
        "status": "running"
    }
