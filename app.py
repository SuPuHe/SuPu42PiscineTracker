import os
import time
import json
import requests
from flask import Flask, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ================= CONFIG =================

UID = os.getenv("UID")
SECRET = os.getenv("SECRET")

API_URL = "https://api.intra.42.fr/v2"

HEILBRONN_EMAIL_DOMAIN = "@student.42heilbronn.de"
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

    def get_recent_students(self, pages=5, per_page=100):
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
        return [u for u in users if u.get("email", "").endswith(HEILBRONN_EMAIL_DOMAIN)]

    def get_user_details(self, login):
        """Детальная информация + проверка Piscine"""
        headers = {"Authorization": f"Bearer {self.token}"}
        res = requests.get(f"{API_URL}/users/{login}", headers=headers)

        if res.status_code != 200:
            return None

        data = res.json()

        # Проверка Piscine через cursus slug или name
        level = 0
        is_piscine = False
        for cu in data.get("cursus_users", []):
            cursus = cu.get("cursus", {})
            slug = cursus.get("slug", "").lower()
            name = cursus.get("name", "").lower()
            if "piscine" in slug or "piscine" in name:
                is_piscine = True
                level = cu.get("level", 0)
                break  # берём первый найденный Piscine

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

# ================= CACHE =================

cached_data = []

# ================= ROUTES =================

@app.route('/')
def index():
    global cached_data

    if not cached_data and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cached_data = json.load(f)
        except json.JSONDecodeError:
            cached_data = []

    return render_template("index.html", students=cached_data)

@app.route('/refresh')
def refresh_data():
    global cached_data
    api = IntraAPI()

    print("Fetching recent students...")
    recent_users = api.get_recent_students(pages=5, per_page=100)
    print(f"Recent users fetched: {len(recent_users)}")

    heilbronn_candidates = api.filter_heilbronn_candidates(recent_users)
    print(f"Heilbronn email candidates: {len(heilbronn_candidates)}")

    full_data = []

    for u in heilbronn_candidates:
        login = u.get("login")
        print(f"Checking Piscine: {login}")

        details = api.get_user_details(login)
        if details:
            full_data.append(details)

        time.sleep(0.4)

    cached_data = sorted(full_data, key=lambda x: x["level"], reverse=True)

    with open(CACHE_FILE, "w") as f:
        json.dump(cached_data, f, indent=2)

    return f"Loaded {len(cached_data)} Heilbronn Piscine students!"

# ================= RUN =================

if __name__ == "__main__":
    app.run(debug=True, port=5000)
