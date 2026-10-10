import os

import dotenv
from flask import Flask
from flask_caching import Cache

from epadata.routes import blueprints
from epadata import globals, search  # NEW: search = full-text search (Whoosh)


def create_app(test_config=None):
    dotenv.load_dotenv()

    globals.app = Flask(__name__, instance_relative_config=True)
    globals.app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        DB_URI="sqlite:///epadata/db/db.sqlite",
        UPLOADS_DIR="./epadata/data/uploads",
        # NEW: where Whoosh keeps its index. Built from this file's own location, so it
        # does not depend on which folder the app is started from.
        SEARCH_INDEX_DIR=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "search_index"
        ),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,  # NEW: reject uploads over 25 MB
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

    search.init_search(globals.app)  # NEW: open (or build) the search index

    return globals.app



from epadata import routes

__all__ = ["routes"]


if __name__ == "__main__":
    globals.app.run(debug=True)