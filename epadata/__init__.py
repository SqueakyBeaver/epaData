import os

import dotenv
from flask import Flask

def create_app(test_config=None):
    dotenv.load_dotenv()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        DATABASE=os.path.join(app.instance_path, "flaskr.sqlite"),
    )

    if test_config is None:
        app.config.from_pyfile('config.py', silent=True)
    else:
        app.config.from_mapping(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    return app

app = create_app()

from epadata import routes

__all__ = ["routes"]


if __name__ == "__main__":
    app.run(debug=True)
