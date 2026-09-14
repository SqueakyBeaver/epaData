from os import PathLike

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("sqlite:///db.sqlite")
Session = sessionmaker(engine)


class DBClient:
    def __init__(self, path: str | PathLike = "db.sqlite"):
        self.Session = Session


