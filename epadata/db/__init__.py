from flask import current_app
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import Base

if current_app:
    with current_app.app_context():
        engine = create_engine(current_app.config["DB_URI"] or "sqlite:///db.sqlite")
else:
    engine = create_engine("sqlite:///db.sqlite")

Session = scoped_session(sessionmaker(engine))


Base.metadata.create_all(engine)
