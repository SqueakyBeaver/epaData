import os

import dotenv
from flask import Flask
from flask_caching import Cache

from epadata.routes import blueprints
from epadata import globals


def create_app(test_config=None):
    dotenv.load_dotenv()

    globals.app = Flask(__name__, instance_relative_config=True)
    globals.app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        DB_URI="sqlite:///epadata/db/db.sqlite",
        UPLOADS_DIR="./epadata/data/uploads",
        CACHE_TYPE="SimpleCache",
        CACHE_DEFAULT_TIMEOUT=300,
    )

    if test_config is None:
        globals.app.config.from_pyfile("config.py", silent=True)
    else:
        globals.app.config.from_mapping(test_config)

    os.makedirs(globals.app.instance_path, exist_ok=True)

    for bp in blueprints:
        globals.app.register_blueprint(bp)

    globals.cache = Cache()
    globals.cache.init_app(globals.app)

    return globals.app



from epadata import routes

__all__ = ["routes"]


if __name__ == "__main__":
    globals.app.run(debug=True)
