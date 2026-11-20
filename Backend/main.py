from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from database import engine
import models
from router import auth, books, circulation, wishlist, admin

app = FastAPI(
    title="Library Management System",
    description="Library Management System API",
    version="1.0.0",
)

# -------------------- CORS --------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------- DATABASE --------------------

models.Base.metadata.create_all(bind=engine)

# -------------------- ROUTERS --------------------

app.include_router(auth.router, tags=["Authentication"])
app.include_router(books.router)
app.include_router(circulation.router)
app.include_router(wishlist.router)
app.include_router(admin.router)
