import os
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template
from requests_cache import CachedSession


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "app.db"),
        CACHE_DIR=os.path.join(app.root_path, "cache"),
    )
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["CACHE_DIR"], exist_ok=True)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    def close_db(exc=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    app.get_db = get_db
    app.teardown_appcontext(close_db)

    # --- pages ---
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/sources")
    def sources_page():
        return render_template("sources.html")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)