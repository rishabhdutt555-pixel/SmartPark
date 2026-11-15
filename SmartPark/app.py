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
        # Safe migrations for databases created by earlier project versions.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(spaces)")}
        if "area" not in columns:
            connection.execute("ALTER TABLE spaces ADD COLUMN area TEXT NOT NULL DEFAULT 'General'")
        if "status" not in columns:
            connection.execute("ALTER TABLE spaces ADD COLUMN status TEXT NOT NULL DEFAULT 'available'")
        booking_columns = {row[1] for row in connection.execute("PRAGMA table_info(bookings)")}
        if "booking_type" not in booking_columns:
            connection.execute("ALTER TABLE bookings ADD COLUMN booking_type TEXT NOT NULL DEFAULT 'normal'")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
            user_id INTEGER NOT NULL REFERENCES users(id), amount INTEGER NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'pending' CHECK(payment_status IN ('pending','paid')),
            payment_method TEXT NOT NULL DEFAULT 'demo', created TEXT DEFAULT CURRENT_TIMESTAMP, paid_at TEXT
        );
        CREATE TRIGGER IF NOT EXISTS booking_occupies_space AFTER INSERT ON bookings WHEN NEW.status='active'
        BEGIN UPDATE spaces SET status='occupied' WHERE id=NEW.space_id AND status!='maintenance'; END;
        CREATE TRIGGER IF NOT EXISTS booking_releases_space AFTER UPDATE OF status ON bookings WHEN OLD.status='active' AND NEW.status!='active'
        BEGIN UPDATE spaces SET status='available' WHERE id=OLD.space_id AND status='occupied' AND NOT EXISTS(SELECT 1 FROM bookings WHERE space_id=OLD.space_id AND status='active'); END;
        CREATE TRIGGER IF NOT EXISTS booking_delete_releases_space AFTER DELETE ON bookings
        BEGIN UPDATE spaces SET status='available' WHERE id=OLD.space_id AND status='occupied' AND NOT EXISTS(SELECT 1 FROM bookings WHERE space_id=OLD.space_id AND status='active'); END;
        """)
        connection.execute("UPDATE spaces SET status='occupied' WHERE id IN (SELECT space_id FROM bookings WHERE status='active') AND status!='maintenance'")
        connection.execute(
            "INSERT OR IGNORE INTO users(name, email, password, role) VALUES(?,?,?,?)",
            (
                "Admin",
                "admin@gmail.com",
                generate_password_hash("admin123"),
                "admin",
            ),
        )
        for i in range(1, 13):
            connection.execute(
                "INSERT OR IGNORE INTO spaces(label, rate, area) VALUES(?, ?, ?)",
                (f"P-{i:02}", 40, "Ground Floor" if i <= 6 else "First Floor"),
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
            SELECT s.*, (s.status = 'occupied') AS busy
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

                if not vehicle or not space or space["status"] != "available":
                    raise ValueError()

                cursor = connection.execute(
                    "INSERT INTO bookings(user_id, vehicle_id, space_id, hours, amount) VALUES(?, ?, ?, ?, ?)",
                    (session["uid"], vehicle_id, space_id, hours, hours * space["rate"]),
                )
                connection.execute("INSERT INTO payments(booking_id,user_id,amount) VALUES(?,?,?)",
                                   (cursor.lastrowid, session["uid"], hours * space["rate"]))

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
            SELECT b.*, v.plate, s.label, p.payment_status
            FROM bookings b
            LEFT JOIN payments p ON p.booking_id=b.id
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


@app.post("/admin/space/<int:space_id>/status")
def update_space_status(space_id):
    if session.get("role") != "admin":
        return "Admin access required", 403
    status = request.form.get("status", "")
    if status not in ("available", "maintenance"):
        flash("Choose a valid space status.")
    else:
        with connect() as connection:
            active = connection.execute("SELECT 1 FROM bookings WHERE space_id=? AND status='active'", (space_id,)).fetchone()
            if active and status == "available":
                flash("Cancel or complete the active booking before making this space available.")
            elif active and status == "maintenance":
                flash("This space has an active booking; cancel it before setting maintenance.")
            else:
                connection.execute("UPDATE spaces SET status=? WHERE id=?", (status, space_id))
                flash("Space status updated.")
    return redirect("/admin")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("role") != "admin":
        return "Admin access required", 403

    if request.method == "POST":
        try:
            label = request.form.get("label", "").strip().upper()
            rate = int(request.form.get("rate", "0"))
            area = request.form.get("area", "General").strip() or "General"
            if not label or rate <= 0:
                raise ValueError()

            with connect() as connection:
                connection.execute(
                    "INSERT INTO spaces(label, rate, area) VALUES(?, ?, ?)", (label, rate, area)
                )
            flash("Space added.")
        except (ValueError, sqlite3.IntegrityError):
            flash("Use a unique space name and positive rate.")
        return redirect("/admin")

    with connect() as connection:
        spaces = connection.execute("SELECT * FROM spaces ORDER BY id").fetchall()
        rows = connection.execute(
            """
            SELECT b.id, u.name, v.plate, s.label, b.status, b.amount, b.booking_type,
                   COALESCE(p.payment_status, 'pending') AS payment_status
            FROM bookings b
            JOIN users u ON u.id = b.user_id
            JOIN vehicles v ON v.id = b.vehicle_id
            JOIN spaces s ON s.id = b.space_id
            LEFT JOIN payments p ON p.booking_id=b.id
            ORDER BY b.id DESC
            """
        ).fetchall()
    return render_template("admin.html", rows=rows, spaces=spaces)



@app.route("/emergency", methods=["GET", "POST"])
def emergency_parking():
    if request.method == "POST":
        try:
            vehicle_id = int(request.form["vid"])
            hours = int(request.form.get("hours", "1"))
            if not 1 <= hours <= 24:
                raise ValueError()
            with connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                vehicle = connection.execute("SELECT id FROM vehicles WHERE id=? AND user_id=?", (vehicle_id, session["uid"])).fetchone()
                active_booking = connection.execute(
                    "SELECT id FROM bookings WHERE vehicle_id=? AND status='active'", (vehicle_id,)
                ).fetchone() if vehicle else None
                space = connection.execute("SELECT * FROM spaces WHERE status='available' ORDER BY id LIMIT 1").fetchone()
                if not vehicle:
                    flash("Select one of your registered vehicles.")
                elif active_booking:
                    flash("This vehicle already has an active booking. Cancel that booking or choose another vehicle for emergency parking.")
                elif not space:
                    flash("No available space right now. Emergency request could not be allocated.")
                else:
                    cursor = connection.execute("INSERT INTO bookings(user_id,vehicle_id,space_id,hours,amount,booking_type) VALUES(?,?,?,?,?,'emergency')",
                                                (session["uid"], vehicle_id, space["id"], hours, hours*space["rate"]))
                    connection.execute("INSERT INTO payments(booking_id,user_id,amount) VALUES(?,?,?)", (cursor.lastrowid, session["uid"], hours*space["rate"]))
                    flash(f"Emergency parking allocated with priority: {space['label']} ({space['area']}).")
            return redirect("/bookings")
        except sqlite3.IntegrityError:
            flash("This vehicle or parking space was just booked. Please refresh and try another available vehicle or space.")
            return redirect("/emergency")
        except (ValueError, KeyError):
            flash("Please enter valid emergency parking details.")
    with connect() as connection:
        vehicles = connection.execute("SELECT * FROM vehicles WHERE user_id=?", (session["uid"],)).fetchall()
        available = connection.execute("SELECT COUNT(*) FROM spaces WHERE status='available'").fetchone()[0]
    return render_template("emergency.html", vehicles=vehicles, available=available)


@app.get("/payments")
def payments():
    with connect() as connection:
        rows = connection.execute("""SELECT p.*, b.status AS booking_status, s.label
            FROM payments p JOIN bookings b ON b.id=p.booking_id JOIN spaces s ON s.id=b.space_id
            WHERE p.user_id=? ORDER BY p.id DESC""", (session["uid"],)).fetchall()
    return render_template("payments.html", rows=rows)


@app.post("/payment/<int:payment_id>/pay")
def mark_payment_paid(payment_id):
    with connect() as connection:
        result = connection.execute("UPDATE payments SET payment_status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND payment_status='pending'",
                                    (payment_id, session["uid"]))
        flash("Demo payment marked as paid." if result.rowcount else "Pending payment not found.")
    return redirect("/payments")


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



def detect_deadlock(allocation_text, request_text, available_text):
    """Run the OS deadlock-detection algorithm on Allocation/Request/Available."""
    def parse_matrix(value):
        rows = []
        for line in value.strip().splitlines():
            if line.strip():
                rows.append([int(part.strip()) for part in line.split(',')])
        return rows

    allocation = parse_matrix(allocation_text)
    request_matrix = parse_matrix(request_text)
    available = [int(part.strip()) for part in available_text.split(',')]
    process_count = len(allocation)
    resource_count = len(available)
    if not 2 <= process_count <= 6 or not 1 <= resource_count <= 4:
        raise ValueError("Use 2–6 processes and 1–4 resource types.")
    if len(request_matrix) != process_count:
        raise ValueError("Allocation and Request must have the same number of rows.")
    if any(len(row) != resource_count for row in allocation + request_matrix):
        raise ValueError("Every matrix row must have one value per resource type.")
    if any(value < 0 for row in allocation + request_matrix for value in row) or any(value < 0 for value in available):
        raise ValueError("Resource values cannot be negative.")

    work = available[:]
    finish = [all(value == 0 for value in row) for row in allocation]
    sequence = []
    changed = True
    while changed:
        changed = False
        for i in range(process_count):
            if not finish[i] and all(request_matrix[i][j] <= work[j] for j in range(resource_count)):
                work = [work[j] + allocation[i][j] for j in range(resource_count)]
                finish[i] = True
                sequence.append(f"P{i + 1}")
                changed = True
    deadlocked = [f"P{i + 1}" for i, done in enumerate(finish) if not done]
    return sequence, deadlocked


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
    deadlock_result = None
    allocation_input = "1, 0\n0, 1"
    request_input = "0, 1\n1, 0"
    available_input = "0, 0"
    if request.method == "POST":
        task = request.form.get("task", "cpu")
        try:
            if task == "deadlock":
                allocation_input = request.form.get("allocation", allocation_input)
                request_input = request.form.get("request_matrix", request_input)
                available_input = request.form.get("available", available_input)
                sequence, deadlocked = detect_deadlock(allocation_input, request_input, available_input)
                deadlock_result = {"sequence": sequence, "deadlocked": deadlocked}
            elif task == "resource":
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
        except (ValueError, TypeError) as error:
            flash(str(error) or "Invalid input. Please check the values and try again.")
    return render_template("os_lab.html", events=events, schedule=schedule,
                           selected_algorithm=selected_algorithm, burst_input=burst_input,
                           quantum=quantum, deadlock_result=deadlock_result,
                           allocation_input=allocation_input, request_input=request_input,
                           available_input=available_input)


if __name__ == "__main__":
    init()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
