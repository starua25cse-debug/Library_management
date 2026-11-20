from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import or_, func

from models import Users, Books, Reservations, IssueRecords, Admins, Librarians, utc_now
from dependencies import (
    db_dependency,
    user_dependency,
    check_librarian,
    check_only_admin,
    safe_commit,
)
from router.circulation import FINE_PER_DAY

router = APIRouter(tags=["Admin Management & Stats"])


class UpdateUserRole(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        allowed_roles = ["admin", "librarian", "member"]
        cleaned_role = value.strip().lower()
        if cleaned_role not in allowed_roles:
            raise ValueError(f"Role must be one of: {', '.join(allowed_roles)}")
        return cleaned_role


class UpdateUserStatus(BaseModel):
    is_active: bool


@router.get("/admin/users")
def get_all_users(
    user: user_dependency,
    db: db_dependency,
    search: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    check_only_admin(user)

    query = db.query(Users)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Users.username.ilike(term),
                Users.email.ilike(term),
                Users.firstname.ilike(term),
                Users.lastname.ilike(term),
            )
        )

    if role and role.strip():
        query = query.filter(Users.role == role.strip().lower())

    if is_active is not None:
        query = query.filter(Users.is_active == is_active)

    total = query.count()
    users_list = query.order_by(Users.id.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "firstname": u.firstname,
                "lastname": u.lastname,
                "role": u.role,
                "is_active": u.is_active,
            }
            for u in users_list
        ],
    }


@router.put("/admin/users/{user_id}/status")
def update_user_status(
    user: user_dependency,
    db: db_dependency,
    status_data: UpdateUserStatus,
    user_id: int = Path(ge=1),
):
    check_only_admin(user)

    if user["id"] == user_id:
        raise HTTPException(
            status_code=400, detail="You cannot suspend or deactivate your own account"
        )

    target_user = db.query(Users).filter(Users.id == user_id).first()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.is_active = status_data.is_active
    safe_commit(db)

    action = "activated" if status_data.is_active else "suspended"
    return {
        "message": f"User '{target_user.username}' has been {action}",
        "user_id": target_user.id,
        "is_active": target_user.is_active,
    }


@router.put("/admin/users/{user_id}/role")
def change_user_role(
    user: user_dependency,
    db: db_dependency,
    role_data: UpdateUserRole,
    user_id: int = Path(ge=1),
):
    check_only_admin(user)

    if user["id"] == user_id:
        raise HTTPException(
            status_code=400, detail="You cannot change or demote your own role"
        )

    target_user = db.query(Users).filter(Users.id == user_id).first()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_role = role_data.role
    if target_user.role == new_role:
        raise HTTPException(
            status_code=400, detail=f"User already has the role '{new_role}'"
        )

    old_role = target_user.role
    target_user.role = new_role

    existing_admin = db.query(Admins).filter(Admins.user_id == target_user.id).first()
    if new_role == "admin":
        if existing_admin is None:
            db.add(
                Admins(
                    user_id=target_user.id,
                    username=target_user.username,
                    email=target_user.email,
                    promoted_by=user["id"],
                )
            )
    elif existing_admin is not None:
        db.delete(existing_admin)

    existing_librarian = (
        db.query(Librarians).filter(Librarians.user_id == target_user.id).first()
    )
    if new_role == "librarian":
        if existing_librarian is None:
            db.add(
                Librarians(
                    user_id=target_user.id,
                    username=target_user.username,
                    email=target_user.email,
                    promoted_by=user["id"],
                )
            )
    elif existing_librarian is not None:
        db.delete(existing_librarian)

    safe_commit(db)

    return {
        "message": f"User '{target_user.username}' role changed from '{old_role}' to '{new_role}'",
        "user_id": target_user.id,
        "username": target_user.username,
        "previous_role": old_role,
        "current_role": new_role,
    }


@router.get("/admin/stats")
def get_admin_dashboard_stats(user: user_dependency, db: db_dependency):
    check_librarian(user)

    now = utc_now()

    total_book_titles = db.query(func.count(Books.id)).scalar() or 0
    total_book_copies = db.query(func.sum(Books.total_copies)).scalar() or 0
    available_book_copies = db.query(func.sum(Books.available_copies)).scalar() or 0

    total_members = (
        db.query(func.count(Users.id)).filter(Users.role == "member").scalar() or 0
    )
    active_members = (
        db.query(func.count(Users.id))
        .filter(Users.role == "member", Users.is_active == True)
        .scalar()
        or 0
    )

    currently_issued_count = (
        db.query(func.count(IssueRecords.id))
        .filter(IssueRecords.status == "issued")
        .scalar()
        or 0
    )
    pending_reservations_count = (
        db.query(func.count(Reservations.id))
        .filter(Reservations.status == "pending")
        .scalar()
        or 0
    )

    collected_fines = (
        db.query(func.sum(IssueRecords.fine_amount))
        .filter(IssueRecords.fine_paid == True)
        .scalar()
        or 0.0
    )
    pending_fines = (
        db.query(func.sum(IssueRecords.fine_amount))
        .filter(IssueRecords.status == "returned", IssueRecords.fine_paid == False)
        .scalar()
        or 0.0
    )

    overdue_records = (
        db.query(IssueRecords, Books, Users)
        .join(Books, IssueRecords.book_id == Books.id)
        .join(Users, IssueRecords.user_id == Users.id)
        .filter(IssueRecords.status == "issued", IssueRecords.due_date < now)
        .order_by(IssueRecords.due_date.asc())
        .all()
    )

    overdue_books = []
    for issue, book, member in overdue_records:
        overdue_days = (now.date() - issue.due_date.date()).days
        estimated_fine = (
            round(overdue_days * FINE_PER_DAY, 2) if overdue_days > 0 else 0.0
        )
        overdue_books.append(
            {
                "issue_id": issue.id,
                "book_id": book.id,
                "book_title": book.title,
                "user_id": member.id,
                "username": member.username,
                "email": member.email,
                "issue_date": issue.issue_date,
                "due_date": issue.due_date,
                "overdue_days": overdue_days,
                "estimated_fine": estimated_fine,
            }
        )

    popular_books_query = (
        db.query(
            Books.id,
            Books.title,
            Books.author,
            Books.category,
            Books.available_copies,
            func.count(IssueRecords.id).label("borrow_count"),
        )
        .join(IssueRecords, Books.id == IssueRecords.book_id)
        .group_by(Books.id)
        .order_by(func.count(IssueRecords.id).desc())
        .limit(5)
        .all()
    )

    most_read_books = [
        {
            "book_id": b.id,
            "title": b.title,
            "author": b.author,
            "category": b.category,
            "available_copies": b.available_copies,
            "total_borrows": b.borrow_count,
        }
        for b in popular_books_query
    ]

    return {
        "summary": {
            "total_book_titles": total_book_titles,
            "total_book_copies": total_book_copies,
            "available_book_copies": available_book_copies,
            "currently_issued_books": currently_issued_count,
            "overdue_books_count": len(overdue_books),
            "pending_reservations": pending_reservations_count,
            "total_members": total_members,
            "active_members": active_members,
            "collected_fines": round(collected_fines, 2),
            "pending_fines": round(pending_fines, 2),
        },
        "overdue_books": overdue_books,
        "most_read_books": most_read_books,
    }
