CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user'
);
CREATE TABLE IF NOT EXISTS vehicles(
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plate TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS spaces(
    id INTEGER PRIMARY KEY,
    label TEXT UNIQUE NOT NULL,
    rate INTEGER NOT NULL CHECK(rate > 0)
);
CREATE TABLE IF NOT EXISTS bookings(
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    space_id INTEGER NOT NULL REFERENCES spaces(id),
    hours INTEGER CHECK(hours BETWEEN 1 AND 24),
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'cancelled', 'completed')),
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS active_space
ON bookings(space_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS active_vehicle
ON bookings(vehicle_id) WHERE status = 'active';
