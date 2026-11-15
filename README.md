# SmartPark with Deadlock Detection

A Flask and SQLite parking management project with an operating systems lab.

## Features

- User registration and login
- Vehicle registration, parking bookings, and cancellation
- Admin parking space management
- Emergency parking allocation and demo payments
- Deadlock detection using Allocation, Request, and Available resources
- FCFS, SJF, and Round Robin CPU scheduling simulations
- Concurrent resource allocation with a lock

## Run locally

```sh
python -m venv .venv
# Activate your virtual environment, then:
python -m pip install -r requirements.txt
cd SmartPark
python app.py
```

Open http://127.0.0.1:5000 in your browser. The app creates its SQLite database and sample parking spaces at startup.

The built-in demonstration administrator is `admin@gmail.com` with password `admin123`. This is an educational demo; configure a private `SECRET_KEY` and replace demo credentials before any deployment.

The original local database is excluded because it contains user and booking records. Source files are preserved from the supplied archive.

## Existing tests

The supplied `SmartPark/test_project.py` has a `wimport` typo on its first line and an admin test with credentials that differ from `app.py`. These existing issues have not been changed as part of this upload.
