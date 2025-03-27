from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_file, Response
import requests
import os
import logging
import time
import json
import bcrypt
import sqlite3
import csv
from contextlib import closing

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TIMEOUT"] = 1800  # Auto logout after 30 minutes

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Google Apps Script URL (Replace with your actual deployment URL)
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyK0XJG0HBwbg1yqUHhFcAGXq6vGQiFHhDn_QS6N0048K_cG4L6voO5dWnZjEQ2jBX1/exec"

# SQLite Database
DATABASE = "attendance.db"

def init_db():
    """Initialize the SQLite database."""
    with closing(sqlite3.connect(DATABASE)) as conn, conn, closing(conn.cursor()) as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS qr_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mobile TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def add_user(username, password):
    """Add a new user to the database."""
    hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    for _ in range(5):  # Retry mechanism
        try:
            with closing(sqlite3.connect(DATABASE)) as conn, conn, closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
                conn.commit()
                break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                time.sleep(1)
            else:
                raise

def authenticate_user(username, password):
    """Check if username and password match."""
    with closing(sqlite3.connect(DATABASE)) as conn, closing(conn.cursor()) as cursor:
        cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
    if row:
        stored_hash = row[0].encode()
        return bcrypt.checkpw(password.encode(), stored_hash)
    return False

def save_qr_scan(mobile, event_type):
    """Save QR scan data to the database."""
    for _ in range(5):  # Retry mechanism
        try:
            with closing(sqlite3.connect(DATABASE)) as conn, conn, closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO qr_scans (mobile, event_type) VALUES (?, ?)", (mobile, event_type))
                conn.commit()
                break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                time.sleep(1)
            else:
                raise

def export_db_to_csv():
    """Export the SQLite database content to CSV format."""
    with closing(sqlite3.connect(DATABASE)) as conn, closing(conn.cursor()) as cursor:
        # Export users table
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        users_csv = "id,username,password\n"
        for row in users:
            users_csv += ",".join(map(str, row)) + "\n"
        
        # Export qr_scans table
        cursor.execute("SELECT * FROM qr_scans")
        qr_scans = cursor.fetchall()
        qr_scans_csv = "id,mobile,event_type,timestamp\n"
        for row in qr_scans:
            qr_scans_csv += ",".join(map(str, row)) + "\n"
    
    return users_csv, qr_scans_csv

@app.route("/download_db")
def download_db():
    """Download the SQLite database content as CSV files."""
    users_csv, qr_scans_csv = export_db_to_csv()
    
    def generate():
        yield "Users Table\n"
        yield users_csv
        yield "\nQR Scans Table\n"
        yield qr_scans_csv
    
    return Response(generate(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=attendance_data.csv"})

@app.route("/reset_db")
def reset_db():
    """Reset the SQLite database and reinitialize default users."""
    if os.path.exists(DATABASE):
        os.remove(DATABASE)

    init_db()  # Recreate tables

    # Add default users
    default_users = {
        "admin": "admin@ksrct",
        "user123": "admin123@123"
    }
    for i in range(1, 10):
        default_users[f"user{i}"] = f"user{i}_pass{i*111}"

    for username, password in default_users.items():
        try:
            add_user(username, password)
            logging.info(f"✅ User '{username}' added successfully")
        except sqlite3.IntegrityError:
            logging.info(f"⚠️ User '{username}' already exists.")

    return jsonify({"success": True, "message": "Database reset and users added"})

# Initialize the database and add default users
init_db()
default_users = {
    "admin": "admin@ksrct",
    "user123": "admin123@123"
}
for i in range(1, 10):
    default_users[f"user{i}"] = f"user{i}_pass{i*111}"

for username, password in default_users.items():
    try:
        add_user(username, password)
        logging.info(f"✅ User '{username}' added with password: {password}")
    except sqlite3.IntegrityError:
        logging.info(f"User '{username}' already exists.")

def is_session_expired():
    """Check if session is expired."""
    if "last_activity" in session:
        elapsed_time = time.time() - session["last_activity"]
        return elapsed_time > app.config["SESSION_TIMEOUT"]
    return True

@app.before_request
def check_session_timeout():
    """Check session expiration before handling requests."""
    if "logged_in" in session:
        if is_session_expired():
            session.clear()
            return redirect(url_for("login_page"))
        session["last_activity"] = time.time()

@app.route("/")
def login_page():
    return render_template("login.html")

@app.route("/authenticate", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")

    if authenticate_user(username, password):
        session["logged_in"] = True
        session["last_activity"] = time.time()
        return redirect(url_for("scan_qr_page"))

    return jsonify({"success": False, "message": "Invalid username or password"}), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/scan_qr")
def scan_qr_page():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("scan_qr.html")

def send_data(mobile, event_type):
    """Send scanned QR data to Google Apps Script and check if the mobile exists."""
    data = {"mobile": mobile, "eventType": event_type}
    try:
        logging.info(f"📤 Sending data to Google Apps Script: {data}")
        response = requests.post(APPS_SCRIPT_URL, json=data)
        
        logging.info(f"📥 Google Script Response: {response.status_code} - {response.text}")

        if response.status_code == 200:
            try:
                json_response = response.json()
                
                # ✅ Check if mobile number was not found
                if not json_response.get("success", False):
                    error_message = json_response.get("message", "Mobile number not found")
                    logging.error(f"❌ Error: {error_message}")
                    return {"success": False, "message": error_message}
                
                return json_response  # Return success response
                
            except json.JSONDecodeError:
                logging.error("❌ Invalid response format from Google Apps Script")
                return {"success": False, "message": "Invalid response format"}
        
        logging.error(f"❌ HTTP Error from Google Apps Script: {response.status_code}")
        return {"success": False, "message": f"Error: {response.status_code}"}

    except requests.RequestException as e:
        logging.error(f"❌ Network error sending data: {e}")
        return {"success": False, "message": "Network error"}

@app.route("/submit_qr", methods=["POST"])
def submit_qr():
    data = request.get_json()
    
    logging.info(f"Received QR Data: {data}")

    if not data:
        logging.error("No QR data received.")
        return jsonify({"success": False, "message": "No QR data received"}), 400

    mobile = data.get("mobile_number")
    event_type = data.get("event_name")

    if not mobile or not event_type:
        logging.error("Missing mobile or event_type.")
        return jsonify({"success": False, "message": "Invalid QR data"}), 400

    save_qr_scan(mobile, event_type)
    response = send_data(mobile, event_type)

    if response.get("success"):
        return jsonify({"success": True, "message": "QR data successfully submitted"})
    return jsonify(response)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
