from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from models import Books, Reservations, IssueRecords
from dependencies import (
    db_dependency,
    user_dependency,
    check_librarian,
    safe_commit,
    notify_wishlist_users,
)

router = APIRouter(tags=["Books"])


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=200)
    price: float = Field(default=0.0, ge=0)
    total_copies: int = Field(default=1, ge=0)
    cover_image: Optional[str] = None


class BookUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, min_length=1, max_length=150)
    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=200)
    price: Optional[float] = Field(default=None, ge=0)
    total_copies: Optional[int] = Field(default=None, ge=0)
    cover_image: Optional[str] = None


@router.get("/books/all")
def get_all_books(
    db: db_dependency,
    search: Optional[str] = Query(
        default=None, description="Search by title, author, or category"
    ),
    category: Optional[str] = Query(
        default=None, description="Filter by exact category"
    ),
    available_only: bool = Query(
        default=False, description="Show only books with available copies > 0"
    ),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(Books)

    if search and search.strip():
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Books.title.ilike(search_term),
                Books.author.ilike(search_term),
                Books.category.ilike(search_term),
            )
        )

    if category and category.strip():
        query = query.filter(Books.category.ilike(category.strip()))

    if available_only:
        query = query.filter(Books.available_copies > 0)

    total_books = query.count()
    books = query.order_by(Books.id.desc()).offset(offset).limit(limit).all()

    return {
        "total": total_books,
        "limit": limit,
        "offset": offset,
        "books": books,
    }


@router.get("/books/{book_id}")
def get_specific_book(
    user: user_dependency, db: db_dependency, book_id: int = Path(ge=1)
):
    book = db.query(Books).filter(Books.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.post("/admin/create_book", status_code=status.HTTP_201_CREATED)
def create_book(user: user_dependency, db: db_dependency, new_book: BookCreate):
    check_librarian(user)

    book_data = new_book.model_dump()
    for field in ["title", "author", "category"]:
        book_data[field] = book_data[field].strip()
        if not book_data[field]:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty")

    book_model = Books(**book_data, available_copies=new_book.total_copies)

    try:
        db.add(book_model)
        db.commit()
        db.refresh(book_model)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create book")

    return {"message": "Book added successfully", "book_id": book_model.id}


@router.put("/admin/update_book/{book_id}")
def update_book(
    user: user_dependency,
    db: db_dependency,
    update_book: BookUpdate,
    book_id: int = Path(ge=1),
):
    check_librarian(user)

    book = db.query(Books).filter(Books.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    update_data = update_book.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    for field in ["title", "author", "category"]:
        if field in update_data:
            value = update_data[field].strip()
            if not value:
                raise HTTPException(status_code=422, detail=f"{field} cannot be empty")
            update_data[field] = value

    issued_count = (
        db.query(IssueRecords)
        .filter(IssueRecords.book_id == book_id, IssueRecords.status == "issued")
        .count()
    )

    if "total_copies" in update_data:
        new_total = update_data["total_copies"]
        if new_total < issued_count:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reduce total copies below currently issued copies ({issued_count})",
            )
        book.total_copies = new_total
        book.available_copies = new_total - issued_count

    for key, value in update_data.items():
        if key == "total_copies":
            continue
        setattr(book, key, value)

    notify_wishlist_users(db, book)
    safe_commit(db)

    return {
        "message": "Book updated successfully",
        "book_id": book_id,
        "total_copies": book.total_copies,
        "available_copies": book.available_copies,
    }


@router.delete("/admin/delete_book/{book_id}")
def delete_book(user: user_dependency, db: db_dependency, book_id: int = Path(ge=1)):
    check_librarian(user)

    book = db.query(Books).filter(Books.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    has_reservations = (
        db.query(Reservations).filter(Reservations.book_id == book_id).first()
    )
    has_issues = db.query(IssueRecords).filter(IssueRecords.book_id == book_id).first()

    if has_reservations or has_issues:
        raise HTTPException(
            status_code=409,
            detail="This book has reservation or issue history and cannot be deleted",
        )

    try:
        db.delete(book)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not delete book")

    return {"message": "Book deleted successfully"}
