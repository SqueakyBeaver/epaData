from . import details, explorer, index, upload, retrieve  # CHANGED: the separate download page was removed

blueprints = [
    details.bp,
    explorer.bp,
    index.bp,
    upload.bp,
    retrieve.bp
]