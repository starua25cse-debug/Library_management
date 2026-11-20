from typing import Annotated
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from database import get_db
from models import Books, Wishlists, Notifications
from router.auth import get_current_user

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict, Depends(get_current_user)]


def check_librarian(user: dict):
    if not user or user.get("role") not in ["admin", "librarian"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin or librarian can perform this action",
        )


def check_only_admin(user: dict):
    if not user or user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin can perform this action",
        )


def safe_commit(db: Session):
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="A database error occurred")


def notify_wishlist_users(db: Session, book: Books):
    if book.available_copies <= 0:
        return

    waiting_items = (
        db.query(Wishlists)
        .filter(Wishlists.book_id == book.id, Wishlists.notified == False)
        .all()
    )

    for item in waiting_items:
        db.add(
            Notifications(
                user_id=item.user_id,
                book_id=book.id,
                message=f"Good news! '{book.title}' from your wishlist is now back in stock.",
            )
        )
        item.notified = True
