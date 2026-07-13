import os
import time
import json
import requests
from datetime import datetime
from flask import Flask, render_template, redirect, request
from flask_apscheduler import APScheduler
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ================= CONFIG =================

UID = os.getenv("UID")
SECRET = os.getenv("SECRET")

API_URL = "https://api.intra.42.fr/v2"
CAMPUS_ID = 39 

TARGET_DATE = "2026-07-06"

CANDIDATES_FILE = "heilbronn_candidates.json"
CACHE_FILE = "students_cache.json"

class Config:
    SCHEDULER_API_ENABLED = True

app.config.from_object(Config())
scheduler = APScheduler()

# ================= API CLASS =================

class IntraAPI:
    def __init__(self):
        self.token = self._get_token()

    def _get_token(self):
        data = {
            "grant_type": "client_credentials",
            "client_id": UID,
            "client_secret": SECRET
        }
        res = requests.post(f"{API_URL}/oauth/token", data=data)
        return res.json().get("access_token")

    # ---------- DISCOVERY ----------
    def get_recent_heilbronn_users_raw(self, count=100):
        headers = {"Authorization": f"Bearer {self.token}"}

        params = {
            "sort": "-created_at",
            "page[size]": count,
            "page[number]": 1
        }

        url = f"{API_URL}/campus/{CAMPUS_ID}/users"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] API Request: {url}")

        res = requests.get(url, headers=headers, params=params)
        if res.status_code != 200:
            print("API error:", res.text)
            return []

        campus_users = res.json()
        parsed_users = []

        for cu in campus_users:
            if "user" in cu and isinstance(cu["user"], dict):
                user_data = cu["user"]
            else:
                user_data = cu

            login = user_data.get("login")
            created_at = user_data.get("created_at") or cu.get("created_at", "")

            if login:
                parsed_users.append({
                    "login": login,
                    "created_at": created_at
                })

        return parsed_users

    # ---------- TRACKING ----------

    def get_user_details(self, login):
        headers = {"Authorization": f"Bearer {self.token}"}
        res = requests.get(f"{API_URL}/users/{login}", headers=headers)

        if res.status_code != 200:
            return None

        data = res.json()

        level = 0
        is_piscine = False

        for cu in data.get("cursus_users", []):
            cursus = cu.get("cursus", {})
            name = cursus.get("name", "").lower()
            slug = cursus.get("slug", "").lower()

            if "piscine" in name or "piscine" in slug:
                is_piscine = True
                level = cu.get("level", 0)
                break

        if not is_piscine:
            return None

        exams = []
        for p in data.get("projects_users", []):
            if "exam" in p["project"]["name"].lower() and p["status"] == "finished":
                exams.append({
                    "name": p["project"]["name"],
                    "mark": p["final_mark"]
                })

        return {
            "login": login,
            "email": data.get("email"),
            "image": data.get("image", {}).get("link"),
            "level": level,
            "exams": exams
        }

# ================= HELPERS =================

last_update = "Never"

def load_candidates():
    if not os.path.exists(CANDIDATES_FILE):
        return []
    try:
        with open(CANDIDATES_FILE) as f:
            content = f.read().strip()
            if not content:
                return []
            return json.loads(content)
    except json.JSONDecodeError:
        print(f"Warning: file {CANDIDATES_FILE} was corrupted or empty.")
        return []

def save_candidates(logins):
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(sorted(set(logins)), f, indent=2)

def perform_refresh():
    global cached_data, last_update
    api = IntraAPI()
    logins = load_candidates()
    if not logins:
        print("Candidates empty")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-refreshing Piscine levels...")
    full_data = []

    for i, login in enumerate(logins, 1):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Update pisciner: {i}/{len(logins)}: {login}")
        details = api.get_user_details(login)
        if details:
            full_data.append(details)
        time.sleep(0.5)

    cached_data = sorted(full_data, key=lambda x: x["level"], reverse=True)
    last_update = datetime.now().strftime("%H:%M:%S")

    with open(CACHE_FILE, "w") as f:
        json.dump(cached_data, f, indent=2)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-refreshing Piscine levels complete")

# ================= CACHE =================

cached_data = []

# ================= SCHEDULER TASK =================

@scheduler.task('interval', id='refresh_task', seconds=600)
def scheduled_refresh():
    perform_refresh()

# ================= ROUTES =================

@app.route("/")
def index():
    global cached_data, last_update

    sort_by = request.args.get('sort', 'level')

    if not cached_data and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cached_data = json.load(f)
            mtime = os.path.getmtime(CACHE_FILE)
            last_update = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
        except json.JSONDecodeError:
            cached_data = []

    def get_exam_mark(student, exam_name):
        for e in student.get('exams', []):
            if exam_name.lower() in e['name'].lower():
                return e['mark']
        return -1

    if sort_by == 'level':
        display_data = sorted(cached_data, key=lambda x: x['level'], reverse=True)
    elif sort_by.startswith('exam'):
        exam_num = sort_by.replace('exam', '')
        search_name = "Final Exam" if exam_num == "03" else f"Exam {exam_num}"
        display_data = sorted(cached_data, key=lambda x: get_exam_mark(x, search_name), reverse=True)
    else:
        display_data = sorted(cached_data, key=lambda x: x['level'], reverse=True)

    return render_template("index.html", students=display_data, last_update=last_update, current_sort=sort_by)

@app.route("/rescan")
def rescan_students():
    api = IntraAPI()

    print(f"Rescanning recent Heilbronn students and filtering by date {TARGET_DATE}...")

    raw_users = api.get_recent_heilbronn_users_raw(count=100)

    if not raw_users:
        return f"No students found in campus {CAMPUS_ID} or API error."

    filtered_logins = [
        u["login"]
        for u in raw_users
        if u["created_at"].startswith(TARGET_DATE)
    ]

    if not filtered_logins:
        return f"Processed 100 users, but found 0 users registered on {TARGET_DATE}."

    existing_logins = load_candidates()
    combined_logins = list(set(existing_logins + filtered_logins))

    save_candidates(combined_logins)

    return f"Successfully filtered! Found {len(filtered_logins)} users from {TARGET_DATE}. Total unique monitored users: {len(combined_logins)}."

# ---------- REFRESH ----------

@app.route("/refresh")
def refresh_data():
    perform_refresh()
    return redirect("/")

# ================= RUN =================

if __name__ == "__main__":
    scheduler.init_app(app)
    scheduler.start()
    app.run(host='0.0.0.0', debug=True, port=5173, use_reloader=False)