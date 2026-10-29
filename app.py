import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, session, redirect, flash, render_template
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-educational-demo-only")
DB = os.path.join(os.path.dirname(__file__), "parking.db")
mutex = threading.Lock()

def connect():
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init():
    with connect() as connection:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            connection.executescript(schema_file.read())
        connection.execute(
            "INSERT OR IGNORE INTO users(name, email, password, role) VALUES(?,?,?,?)",
            (
                "Admin",
                "admin@parking.local",
                generate_password_hash("Admin123!"),
                "admin",
            ),
        )
        for i in range(1, 13):
            connection.execute(
                "INSERT OR IGNORE INTO spaces(label, rate) VALUES(?, ?)",
                (f"P-{i:02}", 40),
            )


@app.before_request
def guard():
    if request.endpoint not in ("login", "register", "static") and not session.get("uid"):
        return redirect("/login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or "@" not in email or len(password) < 8:
            flash("Use a name, valid email and password of at least 8 characters.")
        else:
            try:
                with connect() as connection:
                    connection.execute(
                        "INSERT INTO users(name, email, password) VALUES(?,?,?)",
                        (name, email, generate_password_hash(password)),
                    )
                flash("Registered. Please log in.")
                return redirect("/login")
            except sqlite3.IntegrityError:
                flash("Email already registered.")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        with connect() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE email = ?",
                (request.form.get("email", "").strip().lower(),),
            ).fetchone()

        if user and check_password_hash(user["password"], request.form.get("password", "")):
            session.clear()
            session.update(uid=user["id"], name=user["name"], role=user["role"])
            return redirect("/")

        flash("Incorrect email or password.")

    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
def home():
    with connect() as connection:
        spaces = connection.execute(
            """
            SELECT s.*,
                   EXISTS(
                       SELECT 1 FROM bookings b
                       WHERE b.space_id = s.id AND b.status = 'active'
                   ) AS busy
            FROM spaces s
            ORDER BY s.id
            """
        ).fetchall()
        vehicles = connection.execute(
            "SELECT * FROM vehicles WHERE user_id = ?",
            (session["uid"],),
        ).fetchall()

    free_count = sum(not space["busy"] for space in spaces)
    return render_template(
        "home.html", spaces=spaces, vehicles=vehicles, free=free_count
    )


@app.route("/vehicles", methods=["GET", "POST"])
def vehicles():
    if request.method == "POST":
        plate = request.form.get("plate", "").strip().upper()
        if not plate or len(plate) > 20:
            flash("Enter a vehicle number, maximum 20 characters.")
        else:
            try:
                with connect() as connection:
                    connection.execute(
                        "INSERT INTO vehicles(user_id, plate) VALUES(?, ?)",
                        (session["uid"], plate),
                    )
                flash("Vehicle added.")
            except sqlite3.IntegrityError:
                flash("Vehicle already registered.")
        return redirect("/vehicles")

    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM vehicles WHERE user_id = ? ORDER BY id",
            (session["uid"],),
        ).fetchall()

    return render_template("vehicles.html", rows=rows)


@app.post("/book")
def book():
    try:
        space_id = int(request.form["sid"])
        vehicle_id = int(request.form["vid"])
        hours = int(request.form["hours"])

        if not 1 <= hours <= 24:
            raise ValueError()

        with mutex:
            with connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                vehicle = connection.execute(
                    "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
                    (vehicle_id, session["uid"]),
                ).fetchone()
                space = connection.execute(
                    "SELECT * FROM spaces WHERE id = ?", (space_id,)
                ).fetchone()

                if not vehicle or not space:
                    raise ValueError()

                connection.execute(
                    """
                    INSERT INTO bookings(user_id, vehicle_id, space_id, hours, amount)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        session["uid"],
                        vehicle_id,
                        space_id,
                        hours,
                        hours * space["rate"],
                    ),
                )

        flash("Parking space booked successfully.")
    except sqlite3.IntegrityError:
        flash("Space or vehicle already booked. Choose another.")
    except (ValueError, KeyError):
        flash("Invalid booking details.")
    except sqlite3.OperationalError:
        flash("Database busy; try again shortly.")

    return redirect("/bookings")


@app.get("/bookings")
def bookings():
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT b.*, v.plate, s.label
            FROM bookings b
            JOIN vehicles v ON v.id = b.vehicle_id
            JOIN spaces s ON s.id = b.space_id
            WHERE b.user_id = ?
            ORDER BY b.id DESC
            """,
            (session["uid"],),
        ).fetchall()

    return render_template("bookings.html", rows=rows)


@app.post("/booking/<int:bid>/cancel")
def cancel_booking(bid):
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        booking = connection.execute(
            "SELECT id FROM bookings WHERE id = ? AND user_id = ? AND status = 'active'",
            (bid, session["uid"]),
        ).fetchone()
        if not booking:
            flash("Active booking not found.")
        else:
            connection.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,)
            )
            flash("Booking cancelled; parking space released.")
    return redirect("/bookings")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("role") != "admin":
        return "Admin access required", 403

    if request.method == "POST":
        try:
            label = request.form.get("label", "").strip().upper()
            rate = int(request.form.get("rate", "0"))
            if not label or rate <= 0:
                raise ValueError()

            with connect() as connection:
                connection.execute(
                    "INSERT INTO spaces(label, rate) VALUES(?, ?)", (label, rate)
                )
            flash("Space added.")
        except (ValueError, sqlite3.IntegrityError):
            flash("Use a unique space name and positive rate.")
        return redirect("/admin")

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT b.id, u.name, v.plate, s.label, b.status, b.amount
            FROM bookings b
            JOIN users u ON u.id = b.user_id
            JOIN vehicles v ON v.id = b.vehicle_id
            JOIN spaces s ON s.id = b.space_id
            ORDER BY b.id DESC
            """
        ).fetchall()
    return render_template("admin.html", rows=rows)


def simulate(mode, count):
    """Demonstrate synchronized access to one shared parking resource."""
    events = []
    lock = threading.Lock()
    resource_available = [True]

    def worker(request_number):
        with lock:
            if resource_available[0]:
                result = "ALLOCATED the demo parking space"
                resource_available[0] = False
            else:
                result = "WAIT/REJECT: resource already allocated"
            events.append(
                f"Request {request_number} | "
                f"{threading.current_thread().name} | {result}"
            )

    barrier = threading.Barrier(count)

    def concurrent_worker(i):
        barrier.wait(timeout=10)
        worker(i)

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(concurrent_worker, range(1, count + 1)))
    return events


def schedule_cpu(algorithm, burst_values, quantum=2):
    """Simulate CPU scheduling and return a timeline and per-process metrics."""
    processes = [{"pid": f"P{i+1}", "burst": burst, "remaining": burst,
                  "start": None, "completion": 0}
                 for i, burst in enumerate(burst_values)]
    timeline = []
    time = 0

    if algorithm == "fcfs":
        order = processes[:]
        for proc in order:
            proc["start"] = time
            timeline.append({"pid": proc["pid"], "start": time, "end": time + proc["burst"]})
            time += proc["burst"]
            proc["completion"] = time
    elif algorithm == "sjf":
        order = sorted(processes, key=lambda proc: (proc["burst"], int(proc["pid"][1:])))
        for proc in order:
            proc["start"] = time
            timeline.append({"pid": proc["pid"], "start": time, "end": time + proc["burst"]})
            time += proc["burst"]
            proc["completion"] = time
    elif algorithm == "rr":
        queue = processes[:]
        while queue:
            proc = queue.pop(0)
            if proc["start"] is None:
                proc["start"] = time
            run_for = min(quantum, proc["remaining"])
            timeline.append({"pid": proc["pid"], "start": time, "end": time + run_for})
            time += run_for
            proc["remaining"] -= run_for
            if proc["remaining"] == 0:
                proc["completion"] = time
            else:
                queue.append(proc)
    else:
        raise ValueError("Unknown scheduling algorithm")

    rows = []
    for proc in processes:
        turnaround = proc["completion"]
        waiting = turnaround - proc["burst"]
        rows.append({"pid": proc["pid"], "burst": proc["burst"],
                     "completion": proc["completion"], "turnaround": turnaround,
                     "waiting": waiting})
    avg_waiting = round(sum(row["waiting"] for row in rows) / len(rows), 2)
    avg_turnaround = round(sum(row["turnaround"] for row in rows) / len(rows), 2)
    return timeline, rows, avg_waiting, avg_turnaround


@app.route("/os", methods=["GET", "POST"])
def os_part():
    events = []
    schedule = None
    selected_algorithm = "fcfs"
    burst_input = "5, 3, 1, 4"
    quantum = 2
    if request.method == "POST":
        task = request.form.get("task", "cpu")
        try:
            if task == "resource":
                count = int(request.form.get("count", "5"))
                if not 2 <= count <= 20:
                    raise ValueError()
                events = simulate("concurrent", count)
            else:
                selected_algorithm = request.form.get("algorithm", "fcfs")
                burst_input = request.form.get("bursts", "5, 3, 1, 4")
                burst_values = [int(value.strip()) for value in burst_input.split(",") if value.strip()]
                quantum = int(request.form.get("quantum", "2"))
                if not 1 <= len(burst_values) <= 8 or any(value < 1 or value > 20 for value in burst_values):
                    raise ValueError()
                if selected_algorithm not in ("fcfs", "sjf", "rr") or not 1 <= quantum <= 10:
                    raise ValueError()
                schedule = schedule_cpu(selected_algorithm, burst_values, quantum)
        except (ValueError, TypeError):
            flash("Check the inputs: use 1–8 burst times (each 1–20), a valid algorithm, and quantum 1–10.")
    return render_template("os_lab.html", events=events, schedule=schedule,
                           selected_algorithm=selected_algorithm, burst_input=burst_input,
                           quantum=quantum)


if __name__ == "__main__":
    init()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
