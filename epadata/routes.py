from flask import render_template

from epadata import app


@app.route("/")
def home_page():
    return render_template("index.html")
