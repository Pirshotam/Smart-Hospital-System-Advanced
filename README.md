# Smart Hospital Bed & Emergency Capacity System (Streamlit Cloud edition)

One Streamlit app with an embedded SQLite database. **No separate backend, no database server, no
secrets** - deploy it by pointing Streamlit Community Cloud at `frontend/app.py`. The database creates
and fills itself with demo data the first time the app starts.

```
Need -> Search -> Capacity check -> Smart match -> Referral (bed held 15 min) -> Hospital accepts
     -> Patient transferred -> Admitted -> Capacity updated -> Analytics updated
```

## Deploy on Streamlit Community Cloud (free)

1. Push this folder to a GitHub repository (private is fine).
2. Go to https://share.streamlit.io -> **Create app** -> choose the repository and branch.
3. **Main file path:** `frontend/app.py`
4. *(Advanced settings)* pick Python 3.12 or 3.13. **No secrets are needed.**
5. **Deploy.** The first start takes a few seconds longer while the demo database is created.

## Run on your computer

```
pip install -r requirements.txt
streamlit run frontend/app.py
```
(no virtual environment needed). Locally the database file is kept in `frontend/data/`, so your changes
survive restarts; delete that folder (or use *Reset demo data* in the admin Overview) to start fresh.

## Demo accounts (password `Demo@1234`)

| Role | Email | What to try |
|---|---|---|
| Administrator | admin@demo.local | approve users, verify hospitals, analytics, review flagged updates |
| Emergency coordinator | coordinator@demo.local | search, refer, dispatch an ambulance, regional heatmap |
| Patient / attendant | patient@demo.local | search and track a request |
| Hospital staff | staff1@demo.local ... staff7@demo.local | capacity, referrals, admitted patients, forecast |
| Hospital staff (pending) | staff8@demo.local | log in as admin first: approve the user and verify "New Horizon Clinic" |
| Coordinator (pending) | newcoord@demo.local | needs admin approval |

## Try the full flow

1. As **coordinator**: *Find a hospital* -> ICU bed + ventilator from Latifabad -> **Search**. Hospitals without a
   free ICU bed or ventilator show "Not suitable" with the reason. Tick *Compare* on two hospitals. Or paste a case
   description into the **AI case assistant** to fill in the filters.
2. **Send referral** to the best match: a bed (and ventilator) is held for 15 minutes.
3. As that hospital's **staff** (staff1 = Indus City): *Referrals* -> Start review -> **Accept** (the hold restarts).
4. As coordinator: *My requests* -> **Dispatch ambulance** -> watch the live tracking map; show the **QR code**.
5. As staff: enter the code under the QR (or *Confirm transfer*) -> **Admit**. The bed becomes occupied.
   *Admitted patients* -> **Discharge** frees it. A hold that runs out is released automatically.
6. As **admin**: Overview, Hospitals, Users, Review & audit, Analytics (trends, demand forecasting, capacity prediction).

## Folder structure

```
Smart-Hospital-System-Streamlit-Cloud/
├── README.md
├── requirements.txt                 packages (streamlit, pandas, segno)
├── .gitignore
└── frontend/
    ├── app.py                       entry point: login gate, role-based menu
    ├── api.py                       the pages' "API" - runs inside the app, no server
    ├── requirements.txt
    ├── views/                       the pages
    │   ├── auth_view.py             login, registration, account
    │   ├── find_view.py             search, AI assistant, comparison, send referral
    │   ├── requests_view.py         my requests, QR code, ambulance tracking
    │   ├── map_view.py              live hospital map, regional heatmap
    │   ├── hospital_view.py         hospital staff pages
    │   ├── admin_view.py            administrator pages
    │   ├── analytics_view.py        chart panels
    │   └── common.py                shared helpers
    ├── hospital_core/               the application logic
    │   ├── db.py                    SQLite connection, transactions, automatic first-run setup
    │   ├── schema.py                tables
    │   ├── seed.py                  demo data
    │   ├── security.py              password hashing and rules
    │   ├── router.py                maps each call to a handler and enforces roles
    │   ├── validate.py              input checks
    │   ├── maintenance.py           releases expired holds, stores hourly capacity snapshots
    │   ├── handlers/                one file per area: auth, search, referrals, hospital, admin, analytics, ai, notifications
    │   └── services/                matcher, reservations, forecasting, ai_tools, notifier, tracking
    └── tests/test_core.py           end-to-end tests (run: cd frontend && python -m unittest discover -s tests -v)
```

How the parts connect: a page calls `api.get/post(...)` -> `hospital_core/router.py` checks the logged-in
user's role -> a handler in `handlers/` -> `services/` -> `db.py` -> SQLite.

## What it does (from the project brief)

* Live capacity per resource (general, emergency, ICU, NICU, ventilators, operation theatres, isolation, dialysis,
  trauma, ambulances) with total / occupied / reserved / unavailable, last-updated time and an "outdated" warning.
* Search + filters (bed type, ICU, ventilator, hospital, distance, specialist, emergency), hospital comparison,
  smart matching with match %, "Not suitable" reasons, best-match and nearest badges, travel time and wait time.
* Referral workflow with every status (Searching -> Request Sent -> Hospital Reviewing -> Accepted -> Patient
  Transferred -> Admitted, plus Rejected, Cancelled, Expired, No Capacity); no double booking; 15-minute holds.
* AI: case classification, referral summarization, capacity prediction, 24-hour capacity forecast, demand
  forecasting with peak emergency periods, hospital wait-time prediction (rule-based and statistical, no API key).
* Hospital dashboard, admitted-patient management, service and emergency-department availability.
* Admin: manage hospitals and users, approve accounts, verify hospitals, review suspicious capacity updates
  (approve or revert), records review, audit log, login log, system analytics, regional heatmap.
* Live hospital map, QR-based arrival confirmation, simulated ambulance tracking, in-app notifications.

## Security

* Passwords are hashed (PBKDF2-SHA256 + salt); 5 wrong passwords lock an account for 10 minutes; sessions end after 8 hours.
* Patients can use the app at once; coordinators and hospital staff need **administrator approval** to log in.
* A hospital must be **verified** by an admin before it appears in searches or its staff can change anything.
* Every action is checked against the user's role on the server side (`router.py`). Patients see only their own
  requests, staff only their own hospital, and only the sender sees the QR code.

## Good to know

* **The data is a demo.** On Streamlit Community Cloud the database file lives on the server and is lost when the
  app restarts or is redeployed; it then rebuilds itself from the demo data. For permanent data, switch `db.py`
  to a hosted database.
* SQLite allows one writer at a time, which is also what prevents two people from taking the same last bed.
  It is plenty for a demo and a small team.
* Holds are released and hourly snapshots are stored whenever someone uses the app (there is no always-on worker).
* SMS/push are not real; notifications appear inside the app (`hospital_core/services/notifier.py` is the place
  to plug in an SMS service).
* **After pushing new code to GitHub**, open the app -> *Manage app* -> **Reboot app**, so the server restarts
  with the new code. If the database format changed, it is rebuilt automatically from the demo data.
* **QR codes:** every referral that was sent to a hospital shows a QR code on the sender's *My requests* page. It
  contains a link to this app; hospital staff scan it with any phone camera, log in, and see the referral and its
  status. When the referral is accepted they press *Confirm patient arrival*. The code can also be typed in under
  *Referrals -> Check a referral code*. (If the app address cannot be detected, set an
  `APP_URL` secret to the app's public address.)
* Change the demo passwords before using the app for anything real.
