from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import config

DATABASE_URL = config.require("DATABASE_URL")

engine = create_engine(
    DATABASE_URL
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
