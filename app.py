from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("WMS_SECRET_KEY", "dev-change-this-key"),
        DATABASE=str(Path(app.instance_path) / "wms.db"),
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db() -> None:
        db = get_db()
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed_db(db)
        else:
            db.execute(
                "UPDATE users SET email = ? WHERE email = ?",
                ("admin@lacerdaflux.local", "admin@logicontrol.local"),
            )
            db.commit()

    def login_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            return view(**kwargs)

        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(url_for("index"))
            flash("E-mail ou senha incorretos.", "error")
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html", user_name=session.get("user_name"))

    @app.get("/api/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        metrics = db.execute(
            """
            SELECT
                COALESCE(SUM(s.quantity), 0) AS units,
                COUNT(DISTINCT p.id) AS skus,
                COUNT(DISTINCT CASE WHEN COALESCE(totals.total_qty, 0) <= p.min_stock THEN p.id END) AS low_stock,
                COUNT(DISTINCT CASE WHEN s.expiry_date IS NOT NULL
                    AND date(s.expiry_date) <= date('now', '+30 days') THEN p.id END) AS expiring
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id
            LEFT JOIN (
                SELECT product_id, SUM(quantity) total_qty FROM stock GROUP BY product_id
            ) totals ON totals.product_id = p.id
            """
        ).fetchone()
        divergences = db.execute(
            "SELECT COUNT(*) FROM inventory_counts WHERE difference != 0"
        ).fetchone()[0]
        recent = db.execute(
            """
            SELECT m.id, m.type, m.quantity, m.created_at, p.sku, p.name, l.code AS location
            FROM movements m
            JOIN products p ON p.id = m.product_id
            JOIN locations l ON l.id = m.location_id
            ORDER BY m.id DESC LIMIT 6
            """
        ).fetchall()
        return jsonify(
            metrics={**dict(metrics), "divergences": divergences},
            recent=[dict(row) for row in recent],
        )

    @app.route("/api/products", methods=["GET", "POST"])
    @login_required
    def products():
        db = get_db()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            required = ["sku", "name"]
            if any(not str(data.get(field, "")).strip() for field in required):
                return jsonify(error="SKU e nome sao obrigatorios."), 400
            try:
                cursor = db.execute(
                    "INSERT INTO products (sku, name, unit, min_stock, unit_cost) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(data["sku"]).strip().upper(),
                        str(data["name"]).strip(),
                        str(data.get("unit", "UN")).strip().upper(),
                        max(0, int(data.get("min_stock", 0))),
                        max(0, float(data.get("unit_cost", 0))),
                    ),
                )
                db.commit()
                return jsonify(id=cursor.lastrowid, message="Produto cadastrado."), 201
            except (ValueError, TypeError):
                return jsonify(error="Estoque minimo ou custo invalido."), 400
            except sqlite3.IntegrityError:
                return jsonify(error="Este SKU ja esta cadastrado."), 409

        rows = db.execute(
            """
            SELECT p.*, COALESCE(SUM(s.quantity), 0) AS stock,
                   GROUP_CONCAT(DISTINCT l.code) AS locations
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id
            LEFT JOIN locations l ON l.id = s.location_id AND s.quantity > 0
            GROUP BY p.id ORDER BY p.name
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.route("/api/locations", methods=["GET", "POST"])
    @login_required
    def locations():
        db = get_db()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            code = str(data.get("code", "")).strip().upper()
            description = str(data.get("description", "")).strip()
            if not code:
                return jsonify(error="O codigo do endereco e obrigatorio."), 400
            try:
                cursor = db.execute(
                    "INSERT INTO locations (code, description) VALUES (?, ?)", (code, description)
                )
                db.commit()
                return jsonify(id=cursor.lastrowid, message="Endereco cadastrado."), 201
            except sqlite3.IntegrityError:
                return jsonify(error="Este endereco ja esta cadastrado."), 409

        rows = db.execute(
            """
            SELECT l.*, COUNT(DISTINCT s.product_id) AS skus,
                   COALESCE(SUM(s.quantity), 0) AS units
            FROM locations l LEFT JOIN stock s ON s.location_id = l.id
            GROUP BY l.id ORDER BY l.code
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.route("/api/movements", methods=["GET", "POST"])
    @login_required
    def movements():
        db = get_db()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            try:
                product_id = int(data.get("product_id"))
                location_id = int(data.get("location_id"))
                quantity = int(data.get("quantity"))
            except (TypeError, ValueError):
                return jsonify(error="Produto, endereco e quantidade sao obrigatorios."), 400
            movement_type = str(data.get("type", "ENTRY")).upper()
            if movement_type not in {"ENTRY", "EXIT", "ADJUSTMENT"} or quantity <= 0:
                return jsonify(error="Tipo ou quantidade invalida."), 400
            signed_qty = quantity if movement_type in {"ENTRY", "ADJUSTMENT"} else -quantity
            lot = str(data.get("lot", "")).strip()
            if movement_type == "EXIT" and not lot:
                stock_rows = db.execute(
                    """SELECT id, quantity FROM stock
                    WHERE product_id = ? AND location_id = ? AND quantity > 0
                    ORDER BY expiry_date IS NULL, expiry_date, id""",
                    (product_id, location_id),
                ).fetchall()
                available = sum(row["quantity"] for row in stock_rows)
                if available < quantity:
                    return jsonify(error=f"Saldo insuficiente. Disponivel: {available}."), 409
                remaining = quantity
                for row in stock_rows:
                    deducted = min(row["quantity"], remaining)
                    db.execute(
                        "UPDATE stock SET quantity = quantity - ? WHERE id = ?",
                        (deducted, row["id"]),
                    )
                    remaining -= deducted
                    if remaining == 0:
                        break
            else:
                stock_row = db.execute(
                    "SELECT id, quantity FROM stock WHERE product_id = ? AND location_id = ? AND lot = ?",
                    (product_id, location_id, lot),
                ).fetchone()
                current = stock_row["quantity"] if stock_row else 0
                if current + signed_qty < 0:
                    return jsonify(error=f"Saldo insuficiente. Disponivel: {current}."), 409
                if stock_row:
                    db.execute(
                        "UPDATE stock SET quantity = ?, expiry_date = COALESCE(?, expiry_date) WHERE id = ?",
                        (current + signed_qty, data.get("expiry_date") or None, stock_row["id"]),
                    )
                else:
                    db.execute(
                        "INSERT INTO stock (product_id, location_id, quantity, lot, expiry_date) VALUES (?, ?, ?, ?, ?)",
                        (product_id, location_id, signed_qty, lot, data.get("expiry_date") or None),
                    )
            db.execute(
                "INSERT INTO movements (product_id, location_id, type, quantity, note, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                (product_id, location_id, movement_type, signed_qty, str(data.get("note", "")).strip(), session["user_id"]),
            )
            db.commit()
            return jsonify(message="Movimentacao registrada."), 201

        rows = db.execute(
            """
            SELECT m.*, p.sku, p.name, l.code AS location
            FROM movements m JOIN products p ON p.id = m.product_id
            JOIN locations l ON l.id = m.location_id
            ORDER BY m.id DESC LIMIT 100
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.route("/api/inventory", methods=["GET", "POST"])
    @login_required
    def inventory():
        db = get_db()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            try:
                product_id = int(data.get("product_id"))
                location_id = int(data.get("location_id"))
                counted = max(0, int(data.get("counted_quantity")))
            except (TypeError, ValueError):
                return jsonify(error="Preencha endereco, produto e quantidade."), 400
            system_qty = db.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM stock WHERE product_id = ? AND location_id = ?",
                (product_id, location_id),
            ).fetchone()[0]
            difference = counted - system_qty
            db.execute(
                """
                INSERT INTO inventory_counts
                (product_id, location_id, system_quantity, counted_quantity, difference, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, location_id, system_qty, counted, difference, session["user_id"]),
            )
            db.commit()
            return jsonify(
                message="Contagem registrada.", system_quantity=system_qty,
                counted_quantity=counted, difference=difference
            ), 201

        rows = db.execute(
            """
            SELECT i.*, p.sku, p.name, l.code AS location, u.name AS operator
            FROM inventory_counts i JOIN products p ON p.id = i.product_id
            JOIN locations l ON l.id = i.location_id JOIN users u ON u.id = i.user_id
            ORDER BY i.id DESC LIMIT 100
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    with app.app_context():
        init_db()

    return app


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'UN',
    min_stock INTEGER NOT NULL DEFAULT 0,
    unit_cost REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    lot TEXT NOT NULL DEFAULT '',
    expiry_date TEXT,
    UNIQUE(product_id, location_id, lot)
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    system_quantity INTEGER NOT NULL,
    counted_quantity INTEGER NOT NULL,
    difference INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def seed_db(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Administrador", "admin@lacerdaflux.local", generate_password_hash("admin123")),
    )
    products = [
        ("78945", "Cafe Torrado 500g", "UN", 100, 18.90),
        ("58492", "Acucar Refinado 1kg", "UN", 80, 5.49),
        ("10234", "Leite Integral 1L", "UN", 150, 4.79),
        ("90871", "Coca-Cola 2L", "UN", 60, 9.99),
        ("33210", "Arroz Tipo 1 5kg", "UN", 90, 28.50),
    ]
    db.executemany(
        "INSERT INTO products (sku, name, unit, min_stock, unit_cost) VALUES (?, ?, ?, ?, ?)", products
    )
    locations = [
        ("A-01-02-03", "Rua A / Modulo 01"),
        ("A-02-03-01", "Rua A / Modulo 02"),
        ("B-01-01-02", "Rua B / Modulo 01"),
        ("REC-01", "Area de recebimento"),
    ]
    db.executemany("INSERT INTO locations (code, description) VALUES (?, ?)", locations)
    expiry_soon = (date.today() + timedelta(days=7)).isoformat()
    expiry_later = (date.today() + timedelta(days=180)).isoformat()
    stocks = [
        (1, 1, 320, "250826", expiry_later),
        (2, 2, 74, "260801", expiry_later),
        (3, 3, 186, "L2608", expiry_soon),
        (4, 2, 38, "23432", expiry_soon),
        (5, 1, 112, "AR2607", expiry_later),
    ]
    db.executemany(
        "INSERT INTO stock (product_id, location_id, quantity, lot, expiry_date) VALUES (?, ?, ?, ?, ?)", stocks
    )
    db.executemany(
        "INSERT INTO movements (product_id, location_id, type, quantity, note, user_id) VALUES (?, ?, ?, ?, ?, 1)",
        [
            (1, 1, "ENTRY", 320, "Carga inicial"),
            (2, 2, "ENTRY", 74, "Carga inicial"),
            (3, 3, "ENTRY", 186, "Recebimento NF 1042"),
            (4, 2, "ENTRY", 38, "Recebimento NF 1048"),
            (5, 1, "ENTRY", 112, "Carga inicial"),
        ],
    )
    db.execute(
        """INSERT INTO inventory_counts
        (product_id, location_id, system_quantity, counted_quantity, difference, user_id)
        VALUES (2, 2, 74, 72, -2, 1)"""
    )
    db.commit()


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5055")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
