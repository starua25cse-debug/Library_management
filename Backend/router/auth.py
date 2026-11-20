import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from jose import jwt, JWTError

from database import get_db
from models import Users
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter()

OAuth2_bearer = OAuth2PasswordBearer(tokenUrl="/login")


# -------------------- PASSWORD HASHING HELPERS --------------------


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


# -------------------- PYDANTIC MODELS --------------------


class CreateUsers(BaseModel):
    email: str = Field(min_length=5, max_length=150)
    username: str = Field(min_length=3, max_length=50)
    firstname: str = Field(min_length=1, max_length=50)
    lastname: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=72)


class UpdateUser(BaseModel):
    email: Optional[str] = Field(default=None, min_length=5, max_length=150)
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    firstname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    lastname: Optional[str] = Field(default=None, min_length=1, max_length=50)


class UpdatePassword(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=72)


# -------------------- DATABASE DEPENDENCY --------------------

db_dependency = Annotated[Session, Depends(get_db)]
token_dependency = Annotated[str, Depends(OAuth2_bearer)]


# -------------------- AUTHENTICATION FUNCTIONS --------------------


def authenticate_user(username: str, password: str, db: Session):
    user = db.query(Users).filter(Users.username == username).first()

    if user is None or not user.is_active:
        return False

    try:
        if verify_password(password, user.hash_password):
            return user
    except (ValueError, TypeError):
        return False

    return False


def create_access_token(
    username: str, user_id: int, role: str, expires_delta: timedelta
):
    expires = datetime.now(timezone.utc) + expires_delta
    encode = {"sub": username, "id": user_id, "role": role, "exp": expires}
    return jwt.encode(encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: token_dependency, db: db_dependency):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user_id = payload.get("id")

        if not username or user_id is None:
            raise credentials_exception

        user = (
            db.query(Users)
            .filter(Users.id == user_id, Users.username == username)
            .first()
        )

        if user is None or not user.is_active:
            raise credentials_exception

        return {"id": user.id, "username": user.username, "role": user.role}

    except JWTError:
        raise credentials_exception


user_dependency = Annotated[dict, Depends(get_current_user)]


# -------------------- USER REGISTRATION --------------------


@router.post("/createuser", status_code=status.HTTP_201_CREATED)
def create_users(db: db_dependency, new_user: CreateUsers):
    email = new_user.email.strip().lower()
    username = new_user.username.strip()

    if not email or not username:
        raise HTTPException(
            status_code=400, detail="Email and username cannot be empty"
        )

    if db.query(Users).filter(Users.username == username).first():
        raise HTTPException(status_code=409, detail="Username already exists")

    if db.query(Users).filter(Users.email == email).first():
        raise HTTPException(status_code=409, detail="Email already exists")

    user_model = Users(
        email=email,
        username=username,
        firstname=new_user.firstname.strip(),
        lastname=new_user.lastname.strip(),
        hash_password=hash_password(new_user.password),
        is_active=True,
        role="member",
    )

    try:
        db.add(user_model)
        db.commit()
        db.refresh(user_model)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create user")

    return {"message": "User created successfully", "user_id": user_model.id}


# -------------------- LOGIN --------------------


@router.post("/login")
def login_user(
    db: db_dependency, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
):
    user = authenticate_user(form_data.username, form_data.password, db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        user.username,
        user.id,
        user.role,
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return {"access_token": access_token, "token_type": "bearer"}


# -------------------- UPDATE USER PROFILE --------------------


@router.put("/edituser")
def update_user(user: user_dependency, db: db_dependency, update_user: UpdateUser):
    current_user = db.query(Users).filter(Users.id == user["id"]).first()

    if current_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = update_user.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    if "email" in update_data:
        email = update_data["email"].strip().lower()
        existing_email = (
            db.query(Users)
            .filter(Users.email == email, Users.id != current_user.id)
            .first()
        )
        if existing_email:
            raise HTTPException(status_code=409, detail="Email already exists")
        update_data["email"] = email

    if "username" in update_data:
        username = update_data["username"].strip()
        existing_username = (
            db.query(Users)
            .filter(Users.username == username, Users.id != current_user.id)
            .first()
        )
        if existing_username:
            raise HTTPException(status_code=409, detail="Username already exists")
        update_data["username"] = username

    for key, value in update_data.items():
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise HTTPException(status_code=422, detail=f"{key} cannot be empty")
        setattr(current_user, key, value)

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not update user")

    return {"message": "User updated successfully"}


# -------------------- CHANGE PASSWORD --------------------


@router.put("/passwordchange")
def update_password(
    user: user_dependency, db: db_dependency, update_password: UpdatePassword
):
    current_user = db.query(Users).filter(Users.id == user["id"]).first()

    if current_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(
        update_password.current_password, current_user.hash_password
    ):
        raise HTTPException(status_code=401, detail="Wrong current password")

    if update_password.current_password == update_password.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from current password",
        )

    current_user.hash_password = hash_password(update_password.new_password)

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not update password")

    return {"message": "Password updated successfully"}


# -------------------- GET USER DETAILS --------------------


@router.get("/user")
def get_user_details(user: user_dependency, db: db_dependency):
    current_user = db.query(Users).filter(Users.id == user["id"]).first()

    if current_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "firstname": current_user.firstname,
        "lastname": current_user.lastname,
        "role": current_user.role,
        "is_active": current_user.is_active,
    }
