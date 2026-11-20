from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    CheckConstraint,
    Index,
    UniqueConstraint,
)

from database import Base


def utc_now():
    # Store UTC as timezone-naive datetime for SQLite compatibility.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Users(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, unique=True, nullable=False, index=True)

    firstname = Column(String, nullable=False)
    lastname = Column(String, nullable=False)

    hash_password = Column(String, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    role = Column(String, default="member", nullable=False)
    # admin, librarian, member


class Books(Base):

    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False, index=True)
    author = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)

    description = Column(String, nullable=True)

    price = Column(Float, default=0.0, nullable=False)

    total_copies = Column(Integer, default=5, nullable=False)
    available_copies = Column(Integer, default=5, nullable=False)

    cover_image = Column(String, nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint("price >= 0", name="check_book_price"),
        CheckConstraint("total_copies >= 0", name="check_total_copies"),
        CheckConstraint("available_copies >= 0", name="check_available_copies"),
        CheckConstraint(
            "available_copies <= total_copies",
            name="check_available_not_more_than_total",
        ),
    )


class Reservations(Base):

    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)

    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    reservation_date = Column(DateTime, default=utc_now, nullable=False)

    status = Column(String, default="pending", nullable=False)
    # pending, approved, cancelled, rejected

    __table_args__ = (
        Index("ix_reservation_user_book_status", "user_id", "book_id", "status"),
    )


class IssueRecords(Base):

    __tablename__ = "issue_records"

    id = Column(Integer, primary_key=True, index=True)

    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    issue_date = Column(DateTime, default=utc_now, nullable=False)

    due_date = Column(DateTime, nullable=False)

    return_date = Column(DateTime, nullable=True)

    status = Column(String, default="issued", nullable=False)
    # issued, returned

    fine_amount = Column(Float, default=0.0, nullable=False)

    fine_paid = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        CheckConstraint("fine_amount >= 0", name="check_fine_amount"),
        Index("ix_issue_user_status", "user_id", "status"),
        Index("ix_issue_book_status", "book_id", "status"),
    )


class Admins(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, nullable=False)
    promoted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    promoted_at = Column(DateTime, default=utc_now, nullable=False)


class Librarians(Base):
    __tablename__ = "librarians"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, nullable=False)
    promoted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    promoted_at = Column(DateTime, default=utc_now, nullable=False)


class Wishlists(Base):
    __tablename__ = "wishlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    notified = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_user_book_wishlist"),
    )


class Notifications(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=True)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
