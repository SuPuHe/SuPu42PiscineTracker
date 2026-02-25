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

HEILBRONN_EMAIL_DOMAIN = "@student.42heilbronn.de"

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

    def get_recent_students(self, pages=6, per_page=100):
        headers = {"Authorization": f"Bearer {self.token}"}
        students = []

        for page in range(1, pages + 1):
            params = {
                "filter[kind]": "student",
                "sort": "-created_at",
                "page[size]": per_page,
                "page[number]": page
            }

            res = requests.get(f"{API_URL}/users", headers=headers, params=params)
            if res.status_code != 200:
                print("API error:", res.text)
                break

            users = res.json()
            if not users:
                break

            students.extend(users)
            time.sleep(0.2)

        return students

    def filter_heilbronn_candidates(self, users):
        return [
            u["login"]
            for u in users
            if u.get("email", "").endswith(HEILBRONN_EMAIL_DOMAIN)
        ]

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
    with open(CANDIDATES_FILE) as f:
        return json.load(f)

def save_candidates(logins):
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(sorted(set(logins)), f, indent=2)

def perform_refresh():
    global cached_data, last_update
    api = IntraAPI()
    logins = load_candidates()
    if not logins: return

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

# ---------- RESCAN ----------

@app.route("/rescan")
def rescan_students():
    api = IntraAPI()

    print("Rescanning recent students...")
    recent = api.get_recent_students(pages=5, per_page=100)

    logins = api.filter_heilbronn_candidates(recent)
    save_candidates(logins)

    return f"Found {len(logins)} Heilbronn candidates. Saved."

# ---------- REFRESH ----------

@app.route("/refresh")
def refresh_data():
    perform_refresh()
    return redirect("/")

# ================= RUN =================

if __name__ == "__main__":
    scheduler.init_app(app)
    scheduler.start()
    app.run(host='0.0.0.0', debug=True, port=5000, use_reloader=False)