import os
import time
import json
import requests
from flask import Flask, render_template, redirect
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

def load_candidates():
    if not os.path.exists(CANDIDATES_FILE):
        return []
    with open(CANDIDATES_FILE) as f:
        return json.load(f)

def save_candidates(logins):
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(sorted(set(logins)), f, indent=2)

# ================= CACHE =================

cached_data = []

# ================= ROUTES =================

@app.route("/")
def index():
    global cached_data

    if not cached_data and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cached_data = json.load(f)
        except json.JSONDecodeError:
            cached_data = []

    return render_template("index.html", students=cached_data)

# ---------- RESCAN ----------

@app.route("/rescan")
def rescan_students():
    api = IntraAPI()

    print("Rescanning recent students...")
    recent = api.get_recent_students(pages=10, per_page=100)

    logins = api.filter_heilbronn_candidates(recent)
    save_candidates(logins)

    return f"Found {len(logins)} Heilbronn candidates. Saved."

# ---------- REFRESH ----------

@app.route("/refresh")
def refresh_data():
    global cached_data
    api = IntraAPI()

    logins = load_candidates()
    print(f"Refreshing {len(logins)} students")

    full_data = []

    for login in logins:
        print(f"Updating: {login}")
        details = api.get_user_details(login)
        if details:
            full_data.append(details)
        time.sleep(0.3)

    cached_data = sorted(full_data, key=lambda x: x["level"], reverse=True)

    with open(CACHE_FILE, "w") as f:
        json.dump(cached_data, f, indent=2)

    return redirect("/")

# ================= RUN =================

if __name__ == "__main__":
    app.run(debug=True, port=5000)
