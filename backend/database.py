from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import Config

engine = create_engine(
    Config.DATABASE_URL,
    **(
        # SQLite: same-thread access only for this process.
        {"connect_args": {"check_same_thread": False}}
        if Config.DATABASE_URL.startswith("sqlite")
        else {
            # Postgres (Neon free tier suspends/recycles idle connections): ping
            # each pooled connection before use and recycle before the provider
            # drops it, so a severed pool connection can't 500 a request.
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }
    )
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
