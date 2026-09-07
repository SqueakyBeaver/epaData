# Running

Create a Python virtual environment
```sh
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:
```sh
pip install -r requirements.txt
```

Run the flask application:
```sh
python -m epadata
```

# Codebase overview
## Directory structure
```
epadata/
| general backend python source files
|
|_routes
| | backend http(s) routes
|
|_static/
| | frontend css and js files
|
|_templates/
| | base html for pages
| |
| |_fragments/
| | | html fragments for data that will be updated with htmx
```
## Frontend
- HTML [Jinja2](https://jinja.palletsprojects.com) templates
- [htmx v4](https://four.htmx.org/docs) for easy reactivity
- [Tailwind CSS](https://getbootstrap.com/docs) and [daisyUI](https://daisyui.com)
  because I don't particularly want to write a bunch of CSS/JS for this project

## Backend
- [Flask](https://flask.palletsprojects.com) application
- Uses Flask [blueprints](https://flask.palletsprojects.com/en/stable/tutorial/views/) in the `routes/` directory for routing
  - Note: all routes should return HTML in order for htmx to work properly.
    For routes that might return a JSON response typically,
    use Flask's `render_template()` function to render an HTML fragment

    Example:

    `routes/example.py`
    ```py
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.post("/submit_form")
    def submit_form():
        content = request.form.get("content", "No content submitted :(")
        return render_template("fragments/test.html", content=content)
    ```

    `templates/index.html`
    ```html
    <!-- Pretend there's some boilerplate stuff here -->
    <form hx-post="{{ url_for(".submit_form") }}" hx-target="next output">
        <input name="content" value="empty" />
        <button type="button">Click meeee</button>
    </form>
    <output id="output">OOh will I change???</output>
    ```

    `templates/fragments/test.html`
    ```html
    {% if content %}
        <p>{{ content }}</p>
    {% else %}
        <p>Hm something went wrong</p>
    {% endif %}
    ```
