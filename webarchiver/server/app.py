"""
app.py
======
Flask server for the local web archiver.

Provides:
  - JSON API to list archived sites and their snapshot dates
  - Static serving of archived HTML pages and resources
  - Frontend static file serving

Run:
    python server/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify, abort, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import archive_utils as au

app = Flask(
    __name__,
    static_folder=au.PROJECT_ROOT / "frontend",
    static_url_path="",
)

ARCHIVE_ROOT = au.ARCHIVE_ROOT


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/sites")
def api_sites():
    return jsonify(au.list_sites())


@app.route("/api/sites/<site>/snapshots")
def api_snapshots(site):
    return jsonify(au.list_snapshots(site))


@app.route("/api/sites/<site>/<date>")
def api_snapshot_meta(site, date):
    path = au.resolve_snapshot_path(site, date)
    if path is None:
        abort(404)
    meta = au.read_json(path / "meta.json")
    return jsonify(meta)


# ---------------------------------------------------------------------------
# Archive file serving
# ---------------------------------------------------------------------------

def _resolve(site: str, date: str) -> Path:
    path = au.resolve_snapshot_path(site, date)
    if path is None:
        abort(404)
    return path


@app.route("/archive/<site>/<date>/")
def serve_snapshot_html(site, date):
    path = _resolve(site, date)
    return send_from_directory(path, "index.html")


@app.route("/archive/<site>/<date>/<path:subpath>")
def serve_snapshot_resource(site, date, subpath):
    path = _resolve(site, date)
    return send_from_directory(path, subpath)


# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------

@app.route("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:filename>")
def serve_frontend_static(filename):
    return send_from_directory(app.static_folder, filename)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Archive root: {ARCHIVE_ROOT}")
    app.run(host="127.0.0.1", port=5000, debug=True)
