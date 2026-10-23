from flask import Flask, render_template, request, redirect, session, send_file
import sqlite3
import os
import secrets
from io import BytesIO
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)

app.secret_key = os.environ.get(
    "CLOUDBUS_SECRET_KEY",
    "cloudbus_local_dev_key_change_before_deployment"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DATABASE_DIR, exist_ok=True)

DATABASE = os.path.join(DATABASE_DIR, "cloudbus.db")


# DATABASE CONNECTION
def get_db():

    conn = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    # Enable foreign key support
    conn.execute("PRAGMA foreign_keys = ON")

    # Wait if another booking is writing to the database
    conn.execute("PRAGMA busy_timeout = 30000")

    return conn


# DATABASE INITIALIZATION
def init_db():

    conn = get_db()

    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    # USERS TABLE
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    user_columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    ]

    if "role" not in user_columns:
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN role TEXT DEFAULT 'user'
        """)

    # BOOKINGS TABLE
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            source TEXT,
            destination TEXT,
            travel_date TEXT,
            seats INTEGER
        )
    """)

    booking_columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(bookings)").fetchall()
    ]

    # ADD TOTAL FARE
    if "total_fare" not in booking_columns:
        conn.execute("""
            ALTER TABLE bookings
            ADD COLUMN total_fare INTEGER DEFAULT 0
        """)

    # ADD BOOKING STATUS
    if "status" not in booking_columns:
        conn.execute("""
            ALTER TABLE bookings
            ADD COLUMN status TEXT DEFAULT 'Confirmed'
        """)

    # ADD TICKET CODE
    if "ticket_code" not in booking_columns:
        conn.execute("""
            ALTER TABLE bookings
            ADD COLUMN ticket_code TEXT
        """)

    # ADD REQUEST TOKEN
    if "request_token" not in booking_columns:
        conn.execute("""
            ALTER TABLE bookings
            ADD COLUMN request_token TEXT
        """)

    # UNIQUE TICKET CODE
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_code
        ON bookings(ticket_code)
    """)

    # UNIQUE REQUEST TOKEN
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_request_token
        ON bookings(request_token)
    """)

    # GENERATE REFERENCES FOR OLDER BOOKINGS
    old_tickets = conn.execute("""
        SELECT id FROM bookings
        WHERE ticket_code IS NULL OR ticket_code = ''
    """).fetchall()

    for ticket in old_tickets:
        conn.execute("""
            UPDATE bookings
            SET ticket_code = ?
            WHERE id = ?
        """, (secrets.token_urlsafe(24), ticket["id"]))

    # CREATE ADMIN ACCOUNT
    admin = conn.execute("""
        SELECT * FROM users
        WHERE email = ?
    """, ("admin@cloudbus.com",)).fetchone()

    if admin:
        conn.execute("""
            UPDATE users
            SET role = 'admin'
            WHERE email = ?
        """, ("admin@cloudbus.com",))

    else:
        conn.execute("""
            INSERT INTO users
            (username, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            "Admin",
            "admin@cloudbus.com",
            generate_password_hash("Admin@123"),
            "admin"
        ))

    conn.commit()
    conn.close()


# HOME
@app.route("/")
def home():
    return render_template("index.html")


# REGISTRATION
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or len(password) < 8:
            return "Enter valid details. Password must be at least 8 characters.", 400

        conn = get_db()

        try:
            conn.execute("""
                INSERT INTO users
                (username, email, password, role)
                VALUES (?, ?, ?, ?)
            """, (
                username,
                email,
                generate_password_hash(password),
                "user"
            ))

            conn.commit()
            conn.close()

            return redirect("/login")

        except sqlite3.IntegrityError:
            conn.close()
            return "Email already registered!", 409

    return render_template("register.html")


# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()

        user = conn.execute("""
            SELECT * FROM users
            WHERE email = ?
        """, (email,)).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]

            return redirect("/dashboard")

        return "Invalid email or password!", 401

    return render_template("login.html")


# USER DASHBOARD
@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()

    total_bookings = conn.execute("""
        SELECT COUNT(*) AS total
        FROM bookings
        WHERE user_id = ?
    """, (session["user_id"],)).fetchone()["total"]

    confirmed_bookings = conn.execute("""
        SELECT COUNT(*) AS total
        FROM bookings
        WHERE user_id = ? AND status = 'Confirmed'
    """, (session["user_id"],)).fetchone()["total"]

    total_spent = conn.execute("""
        SELECT COALESCE(SUM(total_fare), 0) AS total
        FROM bookings
        WHERE user_id = ? AND status = 'Confirmed'
    """, (session["user_id"],)).fetchone()["total"]

    conn.close()

    return render_template(
        "dashboard.html",
        total_bookings=total_bookings,
        confirmed_bookings=confirmed_bookings,
        total_spent=total_spent
    )


# ROUTE FARES
ROUTE_FARES = {
    ("Airoli", "Thane"): 50,
    ("Airoli", "Vashi"): 70,
    ("Airoli", "Mumbai"): 100,
    ("Thane", "Vashi"): 80,
    ("Thane", "Mumbai"): 90,
    ("Vashi", "Mumbai"): 100
}


# BOOKING
@app.route("/booking", methods=["GET", "POST"])
def booking():

    if "user_id" not in session:
        return redirect("/login")

    # GENERATE TOKEN WHEN OPENING BOOKING PAGE
    if request.method == "GET":
        session["booking_request_token"] = secrets.token_urlsafe(24)

        return render_template(
            "booking.html",
            request_token=session["booking_request_token"]
        )

    # HANDLE BOOKING SUBMISSION
    source = request.form.get("source")
    destination = request.form.get("destination")
    travel_date = request.form.get("travel_date")

    # Use form token, or session fallback
    request_token = (
        request.form.get("request_token")
        or session.get("booking_request_token")
    )

    if not request_token:
        return "Booking session expired. Please reopen the booking page.", 400

    try:
        seats = int(request.form.get("seats"))
    except (ValueError, TypeError):
        return "Invalid seat selection.", 400

    if seats < 1 or seats > 6:
        return "You can book between 1 and 6 seats.", 400

    if source == destination:
        return "Source and destination cannot be the same.", 400

    try:
        selected_date = date.fromisoformat(travel_date)

        if selected_date < date.today():
            return "Please select a future date.", 400

    except (ValueError, TypeError):
        return "Invalid travel date.", 400

    fare = ROUTE_FARES.get((source, destination))

    if fare is None:
        fare = ROUTE_FARES.get((destination, source))

    if fare is None:
        return "Route not available.", 400

    total_fare = fare * seats
    conn = get_db()

    try:
        # CHECK FOR AN ALREADY PROCESSED REQUEST
        existing = conn.execute("""
            SELECT * FROM bookings
            WHERE request_token = ? AND user_id = ?
        """, (request_token, session["user_id"])).fetchone()

        if existing:
            return render_template(
                "confirmation.html",
                booking_id=existing["id"],
                source=existing["source"],
                destination=existing["destination"],
                travel_date=existing["travel_date"],
                seats=existing["seats"],
                fare_per_seat=existing["total_fare"] // existing["seats"],
                total_fare=existing["total_fare"],
                ticket_code=existing["ticket_code"]
            )

        ticket_code = secrets.token_urlsafe(24)

        cursor = conn.execute("""
            INSERT INTO bookings (
                user_id, source, destination, travel_date,
                seats, total_fare, status, ticket_code, request_token
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"],
            source,
            destination,
            travel_date,
            seats,
            total_fare,
            "Confirmed",
            ticket_code,
            request_token
        ))

        booking_id = cursor.lastrowid
        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()

        existing = conn.execute("""
            SELECT * FROM bookings
            WHERE request_token = ? AND user_id = ?
        """, (request_token, session["user_id"])).fetchone()

        if existing:
            return render_template(
                "confirmation.html",
                booking_id=existing["id"],
                source=existing["source"],
                destination=existing["destination"],
                travel_date=existing["travel_date"],
                seats=existing["seats"],
                fare_per_seat=existing["total_fare"] // existing["seats"],
                total_fare=existing["total_fare"],
                ticket_code=existing["ticket_code"]
            )

        return "Booking could not be completed. Please try again.", 400

    finally:
        conn.close()

    # Clear token after successful booking
    session.pop("booking_request_token", None)

    return render_template(
        "confirmation.html",
        booking_id=booking_id,
        source=source,
        destination=destination,
        travel_date=travel_date,
        seats=seats,
        fare_per_seat=fare,
        total_fare=total_fare,
        ticket_code=ticket_code
    )

# DOWNLOAD SECURE PDF TICKET
@app.route("/download_ticket/<int:booking_id>")
def download_ticket(booking_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()

    booking = conn.execute("""
        SELECT * FROM bookings
        WHERE id = ? AND user_id = ?
    """, (
        booking_id,
        session["user_id"]
    )).fetchone()

    conn.close()

    if booking is None:
        return "Booking not found or access denied.", 404

    if booking["status"] != "Confirmed":
        return "Cancelled tickets cannot be downloaded.", 400

    pdf_buffer = BytesIO()

    pdf = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4
    )

    styles = getSampleStyleSheet()
    content = []

    content.append(Paragraph("<b>CLOUDBUS</b>", styles["Title"]))
    content.append(Spacer(1, 20))

    content.append(Paragraph(
        "<b>BUS TICKET CONFIRMATION</b>",
        styles["Heading2"]
    ))

    content.append(Spacer(1, 20))

    details = [
        f"<b>Booking ID:</b> {booking['id']}",
        f"<b>Ticket Reference:</b> {booking['ticket_code']}",
        f"<b>From:</b> {booking['source']}",
        f"<b>Destination:</b> {booking['destination']}",
        f"<b>Travel Date:</b> {booking['travel_date']}",
        f"<b>Seats:</b> {booking['seats']}",
        f"<b>Fare Per Seat:</b> Rs. {booking['total_fare'] // booking['seats']}",
        f"<b>Total Fare:</b> Rs. {booking['total_fare']}",
        f"<b>Status:</b> {booking['status']}"
    ]

    for detail in details:
        content.append(Paragraph(detail, styles["Normal"]))
        content.append(Spacer(1, 12))

    content.append(Paragraph(
        "Thank you for choosing CloudBus!",
        styles["Italic"]
    ))

    pdf.build(content)

    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"CloudBus_Ticket_{booking['id']}.pdf",
        mimetype="application/pdf"
    )


# TICKET VERIFICATION
@app.route("/verify-ticket/<ticket_code>")
def verify_ticket(ticket_code):

    # Validate ticket reference
    if not ticket_code or len(ticket_code) > 100:
        return render_template(
            "ticket_verification.html",
            valid=False,
            booking=None
        ), 404

    conn = get_db()

    try:
        booking = conn.execute("""
            SELECT
                ticket_code,
                source,
                destination,
                travel_date,
                seats,
                status
            FROM bookings
            WHERE ticket_code = ?
        """, (ticket_code,)).fetchone()

    finally:
        conn.close()

    # Ticket does not exist
    if booking is None:
        return render_template(
            "ticket_verification.html",
            valid=False,
            booking=None
        ), 404

    # Check ticket status
    is_valid = booking["status"] == "Confirmed"

    return render_template(
        "ticket_verification.html",
        valid=is_valid,
        booking=booking
    )


# MY BOOKINGS
@app.route("/my_bookings")
@app.route("/my-bookings")
def my_bookings():

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()

    bookings = conn.execute("""
        SELECT *
        FROM bookings
        WHERE user_id = ?
        ORDER BY id DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "my_bookings.html",
        bookings=bookings
    )


# CANCEL BOOKING
@app.route("/cancel_booking/<int:booking_id>", methods=["POST"])
@app.route("/cancel-booking/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()

    booking = conn.execute("""
        SELECT *
        FROM bookings
        WHERE id = ? AND user_id = ?
    """, (
        booking_id,
        session["user_id"]
    )).fetchone()

    if booking is None:
        conn.close()
        return "Booking not found or access denied.", 404

    if booking["status"] == "Confirmed":

        conn.execute("""
            UPDATE bookings
            SET status = 'Cancelled'
            WHERE id = ? AND user_id = ?
        """, (
            booking_id,
            session["user_id"]
        ))

        conn.commit()

    conn.close()

    return redirect("/my_bookings")


# ADMIN DASHBOARD
@app.route("/admin")
def admin():

    if "user_id" not in session:
        return redirect("/login")

    if session.get("role") != "admin":
        return "Access Denied!", 403

    conn = get_db()

    total_users = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE role = 'user'
    """).fetchone()[0]

    total_bookings = conn.execute("""
        SELECT COUNT(*)
        FROM bookings
    """).fetchone()[0]

    confirmed_bookings = conn.execute("""
        SELECT COUNT(*)
        FROM bookings
        WHERE status = 'Confirmed'
    """).fetchone()[0]

    cancelled_bookings = conn.execute("""
        SELECT COUNT(*)
        FROM bookings
        WHERE status = 'Cancelled'
    """).fetchone()[0]

    total_revenue = conn.execute("""
        SELECT COALESCE(SUM(total_fare), 0)
        FROM bookings
        WHERE status = 'Confirmed'
    """).fetchone()[0]

    bookings = conn.execute("""
        SELECT
            bookings.*,
            users.username,
            users.email
        FROM bookings
        JOIN users ON bookings.user_id = users.id
        ORDER BY bookings.id DESC
    """).fetchall()

    users = conn.execute("""
        SELECT
            users.id,
            users.username,
            users.email,
            COUNT(bookings.id) AS booking_count
        FROM users
        LEFT JOIN bookings
            ON users.id = bookings.user_id
        WHERE users.role = 'user'
        GROUP BY users.id
        ORDER BY users.id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        total_users=total_users,
        total_bookings=total_bookings,
        confirmed_bookings=confirmed_bookings,
        cancelled_bookings=cancelled_bookings,
        total_revenue=total_revenue,
        bookings=bookings,
        users=users
    )


# LOGOUT
@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")


# START APPLICATION
if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )