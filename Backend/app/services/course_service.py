from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.course import Course, Enrollment
from app.models.user import User
from app.schemas.course import CourseCreate


def accessible_course_ids(user_id):
    """Courses taught by or enrolled in by this user; usable inside document queries."""
    enrolled = select(Enrollment.course_id).where(Enrollment.user_id == user_id)
    return select(Course.id).where(or_(Course.instructor_id == user_id, Course.id.in_(enrolled)))


def get_course(db: Session, course_id) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def require_course_membership(db: Session, course_id, user: User) -> Course:
    course = get_course(db, course_id)
    if course.instructor_id != user.id and db.query(Enrollment).filter(
        Enrollment.course_id == course_id, Enrollment.user_id == user.id,
    ).first() is None:
        raise HTTPException(status_code=403, detail="Course enrolment is required")
    return course


def create_course(db: Session, data: CourseCreate, instructor: User) -> Course:
    if instructor.role.name not in {"LECTURER", "ADMIN"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    course = Course(**data.model_dump(), instructor_id=instructor.id)
    db.add(course)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(Course).filter(Course.code == data.code).first():
            raise HTTPException(status_code=409, detail="Course code already exists")
        raise
    db.refresh(course)
    return course


def enroll(db: Session, course_id, student: User) -> Enrollment:
    if student.role.name != "STUDENT":
        raise HTTPException(status_code=403, detail="Only students can self-enrol")
    get_course(db, course_id)
    existing = db.query(Enrollment).filter(Enrollment.course_id == course_id, Enrollment.user_id == student.id).first()
    if existing:
        return existing
    enrollment = Enrollment(course_id=course_id, user_id=student.id)
    db.add(enrollment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Enrollment).filter(Enrollment.course_id == course_id, Enrollment.user_id == student.id).first()
        if existing:
            return existing
        raise
    db.refresh(enrollment)
    return enrollment


def unenroll(db: Session, course_id, student: User):
    get_course(db, course_id)
    enrollment = db.query(Enrollment).filter(Enrollment.course_id == course_id, Enrollment.user_id == student.id).first()
    if enrollment:
        db.delete(enrollment)
        db.commit()
