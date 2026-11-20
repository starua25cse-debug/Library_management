from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from models import Users, Books, Reservations, IssueRecords, utc_now
from dependencies import (
    db_dependency,
    user_dependency,
    check_librarian,
    safe_commit,
    notify_wishlist_users,
)

router = APIRouter(tags=["Circulation (Reserve, Issue & Return)"])

FINE_PER_DAY = 20
LOAN_DAYS = 14


class IssueBook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: int = Field(gt=0)
    user_id: int = Field(gt=0)


def calculate_fine(due_date: datetime, return_date: datetime) -> float:
    overdue_days = (return_date.date() - due_date.date()).days
    if overdue_days > 0:
        return round(overdue_days * FINE_PER_DAY, 2)
    return 0.0


# -------------------- MEMBER RESERVATION & ISSUES --------------------


@router.post("/reserve/{book_id}", status_code=status.HTTP_201_CREATED)
def reserve_book(user: user_dependency, db: db_dependency, book_id: int = Path(ge=1)):
    if user["role"] != "member":
        raise HTTPException(status_code=403, detail="Only members can reserve books")

    user_id = user["id"]
    book = db.query(Books).filter(Books.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    if book.available_copies <= 0:
        raise HTTPException(
            status_code=400, detail="No copies currently available for reservation"
        )

    active_issue = (
        db.query(IssueRecords)
        .filter(
            IssueRecords.book_id == book_id,
            IssueRecords.user_id == user_id,
            IssueRecords.status == "issued",
        )
        .first()
    )
    if active_issue:
        raise HTTPException(status_code=409, detail="You already have this book issued")

    existing_reservation = (
        db.query(Reservations)
        .filter(
            Reservations.book_id == book_id,
            Reservations.user_id == user_id,
            Reservations.status == "pending",
        )
        .first()
    )
    if existing_reservation:
        raise HTTPException(
            status_code=409,
            detail="You already have a pending reservation for this book",
        )

    reservation_model = Reservations(book_id=book_id, user_id=user_id, status="pending")

    try:
        db.add(reservation_model)
        db.commit()
        db.refresh(reservation_model)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create reservation")

    return {
        "message": "Book reserved successfully",
        "reservation_id": reservation_model.id,
        "book_id": book_id,
        "status": reservation_model.status,
    }


@router.delete("/reserve/cancel/{reservation_id}")
def cancel_reservation(
    user: user_dependency, db: db_dependency, reservation_id: int = Path(ge=1)
):
    reservation = (
        db.query(Reservations)
        .filter(Reservations.id == reservation_id, Reservations.user_id == user["id"])
        .first()
    )
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if reservation.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel reservation with status: {reservation.status}",
        )

    reservation.status = "cancelled"
    safe_commit(db)

    return {
        "message": "Reservation cancelled successfully",
        "reservation_id": reservation_id,
        "status": "cancelled",
    }


@router.get("/reserve/my")
def my_reservation(user: user_dependency, db: db_dependency):
    return (
        db.query(Reservations)
        .filter(Reservations.user_id == user["id"])
        .order_by(Reservations.reservation_date.desc())
        .all()
    )


@router.get("/issues/my")
def my_issued_books(user: user_dependency, db: db_dependency):
    return (
        db.query(IssueRecords)
        .filter(IssueRecords.user_id == user["id"], IssueRecords.status == "issued")
        .order_by(IssueRecords.issue_date.desc())
        .all()
    )


# -------------------- LIBRARIAN / ADMIN ISSUE, RETURN & FINE --------------------


@router.post("/admin/create_issue", status_code=status.HTTP_201_CREATED)
def create_issue(user: user_dependency, db: db_dependency, issue_request: IssueBook):
    check_librarian(user)

    book = db.query(Books).filter(Books.id == issue_request.book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    member = db.query(Users).filter(Users.id == issue_request.user_id).first()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    if not member.is_active:
        raise HTTPException(status_code=400, detail="This user account is inactive")

    if member.role != "member":
        raise HTTPException(
            status_code=400, detail="Books can only be issued to members"
        )

    if book.available_copies <= 0:
        raise HTTPException(status_code=400, detail="No copies available")

    existing_issue = (
        db.query(IssueRecords)
        .filter(
            IssueRecords.book_id == issue_request.book_id,
            IssueRecords.user_id == issue_request.user_id,
            IssueRecords.status == "issued",
        )
        .first()
    )
    if existing_issue:
        raise HTTPException(
            status_code=409,
            detail="This member already has an active issue for this book",
        )

    issue_date = utc_now()
    due_date = issue_date + timedelta(days=LOAN_DAYS)

    issue_model = IssueRecords(
        book_id=issue_request.book_id,
        user_id=issue_request.user_id,
        issue_date=issue_date,
        due_date=due_date,
        status="issued",
        fine_amount=0.0,
        fine_paid=True,
    )

    book.available_copies -= 1

    reservation = (
        db.query(Reservations)
        .filter(
            Reservations.book_id == issue_request.book_id,
            Reservations.user_id == issue_request.user_id,
            Reservations.status == "pending",
        )
        .order_by(Reservations.reservation_date.asc())
        .first()
    )
    if reservation is not None:
        reservation.status = "approved"

    try:
        db.add(issue_model)
        db.commit()
        db.refresh(issue_model)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not issue book")

    return {
        "message": "Book issued successfully",
        "issue_id": issue_model.id,
        "book_id": issue_model.book_id,
        "user_id": issue_model.user_id,
        "issue_date": issue_model.issue_date,
        "due_date": issue_model.due_date,
    }


@router.put("/admin/return_book/{issue_id}")
def return_book(user: user_dependency, db: db_dependency, issue_id: int = Path(ge=1)):
    check_librarian(user)

    issue = db.query(IssueRecords).filter(IssueRecords.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue record not found")

    if issue.status == "returned":
        raise HTTPException(
            status_code=400, detail="This book has already been returned"
        )

    book = db.query(Books).filter(Books.id == issue.book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Associated book not found")

    return_date = utc_now()
    fine = calculate_fine(issue.due_date, return_date)

    issue.return_date = return_date
    issue.status = "returned"
    issue.fine_amount = fine
    issue.fine_paid = fine == 0

    if book.available_copies < book.total_copies:
        book.available_copies += 1

    notify_wishlist_users(db, book)
    safe_commit(db)

    return {
        "message": "Book returned successfully",
        "issue_id": issue_id,
        "fine_amount": fine,
        "fine_paid": issue.fine_paid,
    }


@router.put("/admin/fine/pay/{issue_id}")
def fine_paid(user: user_dependency, db: db_dependency, issue_id: int = Path(ge=1)):
    check_librarian(user)

    issue = db.query(IssueRecords).filter(IssueRecords.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue record not found")

    if issue.status != "returned":
        raise HTTPException(
            status_code=400, detail="Book must be returned before paying fine"
        )

    if issue.fine_amount <= 0:
        raise HTTPException(status_code=400, detail="No fine to pay")

    if issue.fine_paid:
        raise HTTPException(status_code=400, detail="Fine has already been paid")

    issue.fine_paid = True
    safe_commit(db)

    return {
        "message": "Fine paid successfully",
        "issue_id": issue_id,
        "fine_amount": issue.fine_amount,
        "fine_paid": True,
    }
