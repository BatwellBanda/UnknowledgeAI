import uuid
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from app.core.security import get_current_user, require_role
from app.models.course import Course
from app.models.user import User
from app.schemas.course import CourseCreate, CourseResponse, EnrollmentResponse
from app.services import course_service
from database.connection import get_db

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.post("", response_model=CourseResponse, status_code=201)
def create(data: CourseCreate, db: Session = Depends(get_db), instructor: User = Depends(require_role("LECTURER", "ADMIN"))):
    return course_service.create_course(db, data, instructor)


@router.get("", response_model=list[CourseResponse])
def list_courses(
    mine: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    query = db.query(Course)
    if mine:
        query = query.filter(Course.id.in_(course_service.accessible_course_ids(current_user.id)))
    return query.order_by(Course.code).offset(offset).limit(limit).all()


@router.get("/{course_id}", response_model=CourseResponse)
def detail(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return course_service.get_course(db, course_id)


@router.post("/{course_id}/enroll", response_model=EnrollmentResponse)
def enroll(course_id: uuid.UUID, db: Session = Depends(get_db), student: User = Depends(require_role("STUDENT"))):
    return course_service.enroll(db, course_id, student)


@router.delete("/{course_id}/enroll", status_code=204)
def unenroll(course_id: uuid.UUID, db: Session = Depends(get_db), student: User = Depends(require_role("STUDENT"))):
    course_service.unenroll(db, course_id, student)
    return Response(status_code=204)
