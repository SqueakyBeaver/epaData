import csv
import io 
import json
import random

from flask import Blueprint, Response, render_template, request

bp = Blueprint("download", __name__, url_prefix="/download")

# then add in query from db.sqlite here 

@bp.route("/", methods=["GET", "POST"])
def index():
    return render_template("download.html")

