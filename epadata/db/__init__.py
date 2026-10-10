from flask import current_app
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import Base

if current_app:
    with current_app.app_context():
        engine = create_engine(current_app.config["DB_URI"] or "sqlite:///db.sqlite")
else:
    engine = create_engine("sqlite:///db.sqlite")

Session = scoped_session(sessionmaker(engine))


Base.metadata.create_all(engine)


# NEW: create_all() only creates MISSING tables; it never adds a column to a table that already
# exists. So a db.sqlite made before the "query_params" column existed gets it added here,
# keeping all of its data (no need to delete db.sqlite).
if "query_params" not in {c["name"] for c in inspect(engine).get_columns("dataset")}:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE dataset ADD COLUMN query_params JSON"))