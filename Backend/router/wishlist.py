from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy.exc import SQLAlchemyError

from models import Books, Wishlists, Notifications
from dependencies import db_dependency, user_dependency, safe_commit

router = APIRouter(tags=["Wishlist & Notifications"])


@router.post("/wishlist/{book_id}", status_code=status.HTTP_201_CREATED)
def add_to_wishlist(
    user: user_dependency, db: db_dependency, book_id: int = Path(ge=1)
):
    if user["role"] != "member":
        raise HTTPException(status_code=403, detail="Only members can use wishlist")

    book = db.query(Books).filter(Books.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    existing = (
        db.query(Wishlists)
        .filter(Wishlists.user_id == user["id"], Wishlists.book_id == book_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Book is already in your wishlist")

    wishlist_item = Wishlists(
        user_id=user["id"],
        book_id=book_id,
        notified=(book.available_copies > 0),
    )

    try:
        db.add(wishlist_item)
        db.commit()
        db.refresh(wishlist_item)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not add book to wishlist")

    return {
        "message": "Book added to wishlist",
        "wishlist_id": wishlist_item.id,
        "book_id": book_id,
        "in_stock": book.available_copies > 0,
    }


@router.get("/wishlist/my")
def get_my_wishlist(user: user_dependency, db: db_dependency):
    items = (
        db.query(Wishlists, Books)
        .join(Books, Wishlists.book_id == Books.id)
        .filter(Wishlists.user_id == user["id"])
        .order_by(Wishlists.created_at.desc())
        .all()
    )

    return [
        {
            "wishlist_id": w.id,
            "added_at": w.created_at,
            "book": {
                "id": b.id,
                "title": b.title,
                "author": b.author,
                "category": b.category,
                "available_copies": b.available_copies,
                "cover_image": b.cover_image,
            },
        }
        for w, b in items
    ]


@router.delete("/wishlist/{book_id}")
def remove_from_wishlist(
    user: user_dependency, db: db_dependency, book_id: int = Path(ge=1)
):
    item = (
        db.query(Wishlists)
        .filter(Wishlists.user_id == user["id"], Wishlists.book_id == book_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Book not found in your wishlist")

    try:
        db.delete(item)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not remove from wishlist")

    return {"message": "Book removed from wishlist"}


@router.get("/notifications/my")
def get_my_notifications(user: user_dependency, db: db_dependency):
    notifications = (
        db.query(Notifications)
        .filter(Notifications.user_id == user["id"])
        .order_by(Notifications.created_at.desc())
        .all()
    )
    unread_count = sum(1 for n in notifications if not n.is_read)

    return {
        "unread_count": unread_count,
        "notifications": notifications,
    }


@router.put("/notifications/{notification_id}/read")
def mark_notification_read(
    user: user_dependency, db: db_dependency, notification_id: int = Path(ge=1)
):
    notif = (
        db.query(Notifications)
        .filter(
            Notifications.id == notification_id, Notifications.user_id == user["id"]
        )
        .first()
    )
    if notif is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.is_read = True
    safe_commit(db)

    return {"message": "Notification marked as read"}
