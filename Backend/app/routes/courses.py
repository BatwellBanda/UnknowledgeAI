import uuid
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from app.core.rate_limit import limit_by_user
from app.core.security import get_current_user, require_role
from app.models.course import Course
from app.models.user import User
from app.schemas.course import (
    CourseCreate, CourseResponse, EnrollmentResponse, EnrollRequest, RosterEntry,
)
from app.services import course_service
from database.connection import get_db

router = APIRouter(prefix="/courses", tags=["Courses"])


def course_view(course: Course, user: User) -> CourseResponse:
    # Only the instructor or an admin sees the enrolment code.
    view = CourseResponse.model_validate(course)
    if not course_service.can_manage(course, user):
        view.join_code = None
    return view


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
    return [course_view(c, current_user) for c in query.order_by(Course.code).offset(offset).limit(limit).all()]


@router.get("/{course_id}", response_model=CourseResponse)
def detail(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return course_view(course_service.get_course(db, course_id), current_user)


@router.post("/{course_id}/join-code", response_model=CourseResponse)
def rotate_join_code(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return course_service.rotate_join_code(db, course_id, current_user)


@router.get("/{course_id}/students", response_model=list[RosterEntry])
def students(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [RosterEntry(user_id=user.id, full_name=user.full_name, email=user.email, enrolled_at=enrollment.created_at)
            for enrollment, user in course_service.roster(db, course_id, current_user)]


@router.delete("/{course_id}/students/{student_id}", status_code=204)
def remove_student(course_id: uuid.UUID, student_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    course_service.remove_student(db, course_id, student_id, current_user)
    return Response(status_code=204)


@router.post("/{course_id}/enroll", response_model=EnrollmentResponse)
def enroll(
    course_id: uuid.UUID, data: EnrollRequest, db: Session = Depends(get_db),
    student: User = Depends(limit_by_user("enroll", 10, 60)),
):
    return course_service.enroll(db, course_id, student, data.join_code)


@router.delete("/{course_id}/enroll", status_code=204)
def unenroll(course_id: uuid.UUID, db: Session = Depends(get_db), student: User = Depends(require_role("STUDENT"))):
    course_service.unenroll(db, course_id, student)
    return Response(status_code=204)
