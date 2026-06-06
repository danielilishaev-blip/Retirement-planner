"""
Personal Retirement Planner  –  with Change Log & Versioning
Run with:  streamlit run retirement_planner.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 REVISION HISTORY  (last 10 revisions, most recent first)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 V1.4  2026-06-06  Retirement – added planned withdrawal events:
                   one-time (at a specific age) and periodic (every
                   N years between from_age and to_age). Each event
                   has its own label, PV amount, base year, and
                   inflation-adjustment toggle. Events shown as
                   markers on the balance chart and included in the
                   year-by-year table.

 V1.3  2026-06-05  Multi-profile support – added login screen
                   with username / password authentication.
                   Passwords are salted and SHA-256 hashed;
                   never stored in plain text. Each user gets
                   their own retirement data file (local:
                   data_{user}.json; cloud: data_{user}.json
                   in the GitHub repo). Profile registry stored
                   in profiles.json. Admin user can delete
                   profiles. Any user can change their own
                   password inside the app.

 V1.2  2026-06-04  Cloud deployment support – added dual storage
                   backend: when GITHUB_TOKEN / GITHUB_REPO secrets
                   are present (Streamlit Cloud) data is read/written
                   to a JSON file in the GitHub repo via the GitHub
                   API; otherwise falls back to local file as before.
                   Added requests + base64 imports.

 V1.1  2026-06-01  Profile tab – renamed "Current Total Savings" to
                   "Initial Total Savings"; added Savings Year and
                   Savings Month fields so the projection knows the
                   exact date that balance was recorded. The projection
                   engine uses those fields as the balance start point
                   instead of always assuming the current month.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import base64
import copy
import hashlib
import json
import os
import secrets
import requests
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Storage helpers ───────────────────────────────────────────────────────────
# Auto-detects local vs Streamlit Cloud via st.secrets.
# Local:  files sit next to this script.
# Cloud:  files live in the GitHub repo, read/written via the GitHub API.
#
# Streamlit secrets needed (Settings → Secrets in Streamlit Cloud):
#   GITHUB_TOKEN = "ghp_xxxxxxxxxxxx"
#   GITHUB_REPO  = "your-username/your-repo"

DATA_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_cloud() -> bool:
    try:
        return bool(st.secrets.get("GITHUB_TOKEN"))
    except Exception:
        return False


def _gh_headers() -> dict:
    return {
        "Authorization": f"token {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github.v3+json",
    }


def _gh_file_url(filename: str) -> str:
    repo = st.secrets["GITHUB_REPO"]
    return f"https://api.github.com/repos/{repo}/contents/{filename}"


def _github_read(filename: str, sha_key: str) -> dict | None:
    """Read a JSON file from GitHub. Caches the SHA in session state."""
    try:
        r = requests.get(_gh_file_url(filename), headers=_gh_headers(), timeout=10)
        if r.status_code == 200:
            body = r.json()
            st.session_state[sha_key] = body["sha"]
            return json.loads(base64.b64decode(body["content"]).decode())
        if r.status_code == 404:
            return None
        st.warning(f"GitHub read {filename} returned {r.status_code}")
    except Exception as e:
        st.warning(f"GitHub read error ({filename}): {e}")
    return None


def _github_write(filename: str, data: dict, sha_key: str) -> bool:
    """Write a JSON file to GitHub (create or update)."""
    try:
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        payload: dict = {
            "message": f"retirement-planner: update {filename} {datetime.now().isoformat(timespec='seconds')}",
            "content": content,
        }
        sha = st.session_state.get(sha_key)
        if sha:
            payload["sha"] = sha
        r = requests.put(_gh_file_url(filename), headers=_gh_headers(), json=payload, timeout=15)
        if r.status_code in (200, 201):
            st.session_state[sha_key] = r.json()["content"]["sha"]
            return True
        st.error(f"GitHub write failed ({r.status_code}): {r.text[:300]}")
    except Exception as e:
        st.error(f"GitHub write error: {e}")
    return False


def _local_read(filename: str) -> dict | None:
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def _local_write(filename: str, data: dict) -> None:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Profile registry (profiles.json) ─────────────────────────────────────────
# Structure: { "profiles": { username: { salt, password_hash, created } } }

PROFILES_FILE = "profiles.json"
PROFILES_SHA  = "_profiles_sha"


def load_profiles() -> dict:
    if _is_cloud():
        data = _github_read(PROFILES_FILE, PROFILES_SHA)
    else:
        data = _local_read(PROFILES_FILE)
    return data if data else {"profiles": {}}


def save_profiles(profiles: dict) -> None:
    if _is_cloud():
        _github_write(PROFILES_FILE, profiles, PROFILES_SHA)
    else:
        _local_write(PROFILES_FILE, profiles)


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (salt_hex, hash_hex). Generate salt if not provided."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return salt, h


def verify_password(profiles: dict, username: str, password: str) -> bool:
    entry = profiles["profiles"].get(username)
    if not entry:
        return False
    _, h = _hash_password(password, salt=entry["salt"])
    return h == entry["password_hash"]


def register_user(profiles: dict, username: str, password: str) -> str | None:
    """Add a new user. Returns error string or None on success."""
    if not username.strip():
        return "Username cannot be empty."
    if username in profiles["profiles"]:
        return "Username already exists."
    if len(password) < 4:
        return "Password must be at least 4 characters."
    salt, h = _hash_password(password)
    profiles["profiles"][username] = {
        "salt": salt,
        "password_hash": h,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    return None


def change_password(profiles: dict, username: str, new_password: str) -> str | None:
    if len(new_password) < 4:
        return "Password must be at least 4 characters."
    salt, h = _hash_password(new_password)
    profiles["profiles"][username]["salt"]          = salt
    profiles["profiles"][username]["password_hash"] = h
    return None


def _user_data_file(username: str) -> str:
    return f"data_{username}.json"


def _user_sha_key(username: str) -> str:
    return f"_sha_{username}"


# ── Per-user retirement data ──────────────────────────────────────────────────

SETTINGS_KEYS = ["profile", "contributions", "growth", "lump_sums", "retirement",
                 "income_streams", "retirement_withdrawals", "snapshots"]


def _settings_copy(data: dict) -> dict:
    return {k: copy.deepcopy(data[k]) for k in SETTINGS_KEYS if k in data}


def _diff_summary(old: dict, new: dict) -> str:
    changes = []
    for section in ["profile", "contributions", "growth", "retirement"]:
        old_s = old.get(section, {})
        new_s = new.get(section, {})
        for key in new_s:
            ov, nv = old_s.get(key), new_s.get(key)
            if ov != nv and ov is not None:
                changes.append(f"{key.replace('_',' ').title()}: {ov} → {nv}")
    for label, key in [("Lump Sums", "lump_sums"), ("Income Streams", "income_streams"),
                        ("Retirement Withdrawals", "retirement_withdrawals"), ("Snapshots", "snapshots")]:
        oa, na = len(old.get(key, [])), len(new.get(key, []))
        if oa != na:
            changes.append(f"{label}: {oa} → {na} entries")
    return "; ".join(changes) if changes else "No field changes detected"


def default_data() -> dict:
    now = datetime.now()
    return {
        "profile": {
            "name": "",
            "birth_year": 1985,
            "birth_month": 6,
            "current_savings": 50000.0,
            "savings_year": now.year,
            "savings_month": now.month,
            "retirement_age": 65,
            "end_age": 95,
            "currency": "USD",
        },
        "contributions": {
            "monthly_contribution": 500.0,
            "yearly_contribution": 0.0,
            "yearly_contribution_month": 1,
            "contribution_growth_rate": 2.0,
        },
        "growth": {"annual_interest_rate": 7.0},
        "lump_sums": [],
        "retirement": {
            "monthly_withdrawal": 3000.0,
            "withdrawal_base_year": now.year,
            "inflation_rate": 2.5,
            "adjust_withdrawal_for_inflation": True,
        },
        "income_streams": [],
        "retirement_withdrawals": [],
        "snapshots": [],
        "changelog": [],
    }


def _migrate(data: dict) -> dict:
    dd = default_data()
    for k, v in dd.items():
        if k not in data:
            data[k] = v
    now = datetime.now()
    prof = data.get("profile", {})
    if "savings_year"  not in prof: prof["savings_year"]  = now.year
    if "savings_month" not in prof: prof["savings_month"] = now.month
    r = data.get("retirement", {})
    if "withdrawal_base_year" not in r: r["withdrawal_base_year"] = now.year
    old_ss    = r.pop("social_security_monthly",    None)
    old_ss_age= r.pop("social_security_start_age",  None)
    old_other = r.pop("other_income_monthly",        None)
    streams   = data.setdefault("income_streams", [])
    existing  = {s["label"] for s in streams}
    if old_ss and float(old_ss) > 0 and "Social Security" not in existing:
        streams.append({"label": "Social Security", "monthly_amount": float(old_ss),
                        "base_year": now.year, "start_age": int(old_ss_age) if old_ss_age else 67,
                        "adjust_for_inflation": True})
    if old_other and float(old_other) > 0 and "Other Income" not in existing:
        streams.append({"label": "Other Income", "monthly_amount": float(old_other),
                        "base_year": now.year, "start_age": int(data["profile"].get("retirement_age", 65)),
                        "adjust_for_inflation": False})
    for s in streams:
        if "base_year" not in s: s["base_year"] = now.year
    data.setdefault("retirement_withdrawals", [])
    return data


def load_user_data(username: str) -> dict:
    fname = _user_data_file(username)
    if _is_cloud():
        raw = _github_read(fname, _user_sha_key(username))
    else:
        raw = _local_read(fname)
    return _migrate(raw) if raw else default_data()


def save_user_data(username: str, data: dict, note: str = "") -> None:
    changelog  = data.setdefault("changelog", [])
    prev       = changelog[-1]["settings"] if changelog else {}
    curr       = _settings_copy(data)
    auto_note  = _diff_summary(prev, curr) if prev else "Initial save"
    final_note = note.strip() if note.strip() else auto_note
    version_num = (changelog[-1]["version"] + 1) if changelog else 1
    changelog.append({
        "version": version_num,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": final_note,
        "settings": curr,
    })
    fname = _user_data_file(username)
    if _is_cloud():
        ok = _github_write(fname, data, _user_sha_key(username))
        if ok:
            st.toast(f"✅ Saved as v{version_num} → GitHub", icon="☁️")
    else:
        _local_write(fname, data)
        st.toast(f"✅ Saved as v{version_num}", icon="💾")


def restore_version(username: str, data: dict, version_num: int) -> None:
    for entry in data["changelog"]:
        if entry["version"] == version_num:
            for k in SETTINGS_KEYS:
                if k in entry["settings"]:
                    data[k] = copy.deepcopy(entry["settings"][k])
            save_user_data(username, data, note=f"Restored from v{version_num}")
            st.rerun()
            return
    st.error(f"Version {version_num} not found.")


# ── Projection Engine ─────────────────────────────────────────────────────────

def add_months(year: int, month: int, n: int):
    month += n
    year  += (month - 1) // 12
    month  = (month - 1) % 12 + 1
    return year, month


def months_between(fy: int, fm: int, ty: int, tm: int) -> int:
    return (ty - fy) * 12 + (tm - fm)


def run_projection(data: dict) -> pd.DataFrame:
    p  = data["profile"];  c = data["contributions"]
    g  = data["growth"];   r = data["retirement"]
    lump_sums           = data["lump_sums"]
    income_streams      = data.get("income_streams", [])
    ret_withdrawals     = data.get("retirement_withdrawals", [])

    birth_year     = int(p["birth_year"]);   birth_month    = int(p["birth_month"])
    retirement_age = int(p["retirement_age"]); end_age       = int(p.get("end_age", 95))
    now            = datetime.now()
    savings_year   = int(p.get("savings_year",  now.year))
    savings_month  = int(p.get("savings_month", now.month))
    if (savings_year, savings_month) > (now.year, now.month):
        savings_year, savings_month = now.year, now.month

    start_year, start_month = savings_year, savings_month
    current_age_months       = (start_year - birth_year) * 12 + (start_month - birth_month)
    retirement_month_offset  = retirement_age * 12 - current_age_months
    end_month_offset         = end_age * 12 - current_age_months
    if end_month_offset <= 0:
        return pd.DataFrame()

    # ── Contribution lump deposit lookup ──
    lump_lookup: dict = {}
    for ls in lump_sums:
        key = (int(ls["year"]), int(ls["month"]))
        lump_lookup[key] = lump_lookup.get(key, 0) + float(ls["amount"])

    monthly_rate           = g["annual_interest_rate"] / 100 / 12
    inflation_monthly      = r["inflation_rate"] / 100 / 12
    contrib_growth_monthly = c["contribution_growth_rate"] / 100 / 12
    yearly_contrib_month   = int(c.get("yearly_contribution_month", 1))
    w_base_year            = int(r.get("withdrawal_base_year", start_year))
    w_base_offset          = months_between(w_base_year, 1, start_year, start_month)

    stream_data = []
    for s in income_streams:
        stream_data.append({
            "monthly_amount":       float(s["monthly_amount"]),
            "start_months_offset":  int(float(s["start_age"]) * 12 - current_age_months),
            "adjust_for_inflation": bool(s.get("adjust_for_inflation", True)),
            "pv_offset":            months_between(int(s.get("base_year", start_year)), 1, start_year, start_month),
        })

    # ── Planned retirement withdrawal lookup: month_offset → (base_amount, pv_offset, adjust) ──
    # Each entry maps a month offset to a list of withdrawal events hitting that month.
    plan_wd_lookup: dict[int, list] = {}

    def _add_plan_wd(month_off: int, base_amt: float, pv_off: int, adj: bool) -> None:
        if 0 <= month_off <= end_month_offset:
            plan_wd_lookup.setdefault(month_off, []).append((base_amt, pv_off, adj))

    for rw in ret_withdrawals:
        base_amt = float(rw["amount"])
        pv_off   = months_between(int(rw.get("base_year", start_year)), 1, start_year, start_month)
        adj      = bool(rw.get("adjust_for_inflation", True))
        if rw["type"] == "one_time":
            m_off = int(float(rw["at_age"]) * 12 - current_age_months)
            _add_plan_wd(m_off, base_amt, pv_off, adj)
        else:  # periodic
            age_cursor = float(rw["from_age"])
            every      = float(rw["every_n_years"])
            to_age     = float(rw["to_age"])
            while age_cursor <= to_age + 1e-9:
                m_off = int(age_cursor * 12 - current_age_months)
                _add_plan_wd(m_off, base_amt, pv_off, adj)
                age_cursor += every

    balance              = float(p["current_savings"])
    base_withdrawal      = float(r["monthly_withdrawal"])
    records = []

    for m in range(0, end_month_offset + 1):
        yr, mo     = add_months(start_year, start_month, m)
        age        = (current_age_months + m) / 12
        is_retired = m >= retirement_month_offset

        interest = balance * monthly_rate
        balance += interest
        contrib = 0.0
        lump    = lump_lookup.get((yr, mo), 0.0)

        if not is_retired:
            gf = (1 + contrib_growth_monthly) ** m
            contrib = float(c["monthly_contribution"]) * gf
            if mo == yearly_contrib_month:
                contrib += float(c.get("yearly_contribution", 0)) * gf
            balance += contrib
        balance += lump

        withdrawal = income = net_withdrawal = 0.0
        planned_wd = 0.0

        if is_retired:
            wm = w_base_offset + m
            withdrawal = (base_withdrawal * ((1 + inflation_monthly) ** wm)
                          if r.get("adjust_withdrawal_for_inflation", True)
                          else base_withdrawal)
            for sd in stream_data:
                if m >= sd["start_months_offset"]:
                    if sd["adjust_for_inflation"]:
                        income += sd["monthly_amount"] * ((1 + inflation_monthly) ** (sd["pv_offset"] + m))
                    else:
                        income += sd["monthly_amount"]
            net_withdrawal = max(0.0, withdrawal - income)
            balance = max(0.0, balance - net_withdrawal)

        # Planned lump withdrawals apply regardless of retirement status
        # (user may schedule a withdrawal before or after retirement)
        if m in plan_wd_lookup:
            for base_amt, pv_off, adj in plan_wd_lookup[m]:
                if adj:
                    planned_wd += base_amt * ((1 + inflation_monthly) ** (pv_off + m))
                else:
                    planned_wd += base_amt
            balance = max(0.0, balance - planned_wd)

        records.append({
            "month_offset": m, "year": yr, "month": mo,
            "age": round(age, 2), "age_int": int(age),
            "balance": round(balance, 2), "interest": round(interest, 2),
            "contribution": round(contrib, 2), "lump_sum": round(lump, 2),
            "withdrawal": round(withdrawal, 2), "income": round(income, 2),
            "net_withdrawal": round(net_withdrawal, 2),
            "planned_withdrawal": round(planned_wd, 2),
            "is_retired": is_retired,
        })
    return pd.DataFrame(records)


# ── App ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Retirement Planner", page_icon="💰", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
h1, h2, h3 { font-family: 'DM Serif Display', serif; }
.stMetric { background: #f8f9fb; border-radius: 10px; padding: 10px 16px; }
.login-box { max-width: 400px; margin: 80px auto; padding: 32px;
             border: 1px solid #e0e0e0; border-radius: 16px;
             background: #fafafa; }
</style>
""", unsafe_allow_html=True)

# ── Login / Register ──────────────────────────────────────────────────────────

if "current_user" not in st.session_state:
    st.session_state.current_user = None
if "profiles_cache" not in st.session_state:
    st.session_state.profiles_cache = None

def get_profiles() -> dict:
    if st.session_state.profiles_cache is None:
        st.session_state.profiles_cache = load_profiles()
    return st.session_state.profiles_cache

if st.session_state.current_user is None:
    st.title("💰 Retirement Planner")
    st.markdown("---")

    profiles = get_profiles()
    is_first_user = len(profiles["profiles"]) == 0

    col_gap1, col_main, col_gap2 = st.columns([1, 2, 1])
    with col_main:
        if is_first_user:
            st.info("👋 No accounts yet. Create the first profile below — it will be the admin account.")

        mode = st.radio("", ["🔑 Login", "➕ Create Profile"], horizontal=True, label_visibility="collapsed")

        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if mode == "🔑 Login":
            if st.button("Login", use_container_width=True, type="primary"):
                if verify_password(profiles, username.strip(), password):
                    st.session_state.current_user = username.strip()
                    st.session_state.data = load_user_data(username.strip())
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

        else:  # Create Profile
            if st.button("Create Profile", use_container_width=True, type="primary"):
                err = register_user(profiles, username.strip(), password)
                if err:
                    st.error(err)
                else:
                    save_profiles(profiles)
                    st.session_state.profiles_cache = profiles
                    st.success(f"Profile **{username.strip()}** created. You can now log in.")
                    st.rerun()
    st.stop()


# ── Logged-in app ─────────────────────────────────────────────────────────────

current_user = st.session_state.current_user
data         = st.session_state.data
profiles     = get_profiles()

# Determine if this user is admin (first registered profile)
admin_user = next(iter(profiles["profiles"])) if profiles["profiles"] else None
is_admin   = (current_user == admin_user)

# Header bar
title_col, user_col = st.columns([5, 2])
title_col.title("💰 Personal Retirement Planner")
with user_col:
    st.markdown(f"<div style='text-align:right;padding-top:18px'>👤 <b>{current_user}</b>"
                + (" 🔑" if is_admin else "") + "</div>", unsafe_allow_html=True)
    if st.button("Logout", key="logout_btn"):
        st.session_state.current_user = None
        st.session_state.data         = None
        st.rerun()


def save_widget(key: str) -> None:
    with st.container():
        note_col, btn_col = st.columns([4, 1])
        note = note_col.text_input("Change note (optional)", value="", key=f"note_{key}",
                                   placeholder="e.g. Increased monthly contribution after raise")
        if btn_col.button("💾 Save", key=f"save_{key}"):
            save_user_data(current_user, data, note=note)


tabs = st.tabs([
    "📊 Dashboard", "👤 Profile", "💵 Contributions", "📈 Growth",
    "🎯 Lump Sums", "🏖️ Retirement", "📸 Snapshots", "📋 Change Log", "⚙️ Account",
])


# ── Tab: Profile ──────────────────────────────────────────────────────────────
with tabs[1]:
    st.header("Profile")
    p = data["profile"]
    col1, col2 = st.columns(2)
    with col1:
        p["name"]        = st.text_input("Your Name", value=p.get("name", ""))
        p["birth_year"]  = st.number_input("Birth Year", 1930, 2010, int(p["birth_year"]), 1)
        p["birth_month"] = st.number_input("Birth Month (1–12)", 1, 12, int(p["birth_month"]), 1)
        st.markdown("**Initial Total Savings**")
        st.caption("Enter the balance and the exact year/month it was recorded.")
        p["current_savings"] = st.number_input("Initial Total Savings ($)", 0.0, value=float(p["current_savings"]), step=1000.0, format="%.2f")
        sv1, sv2 = st.columns(2)
        p["savings_year"]  = sv1.number_input("Savings Year",  2000, 2080, int(p.get("savings_year",  datetime.now().year)), 1)
        p["savings_month"] = sv2.number_input("Savings Month", 1, 12,      int(p.get("savings_month", datetime.now().month)), 1)
    with col2:
        p["retirement_age"] = st.number_input("Planned Retirement Age", 40, 90, int(p["retirement_age"]), 1)
        p["end_age"]        = st.number_input("Projection End Age", 65, 120, int(p.get("end_age", 95)), 1)
        currencies = ["USD", "EUR", "GBP", "ILS", "CAD", "AUD", "JPY", "CHF"]
        curr_sel = p.get("currency", "USD")
        p["currency"] = st.selectbox("Currency", currencies, index=currencies.index(curr_sel) if curr_sel in currencies else 0)
    now_dt = datetime.now()
    age_at_sav = (int(p["savings_year"]) - int(p["birth_year"])) + (int(p["savings_month"]) - int(p["birth_month"])) / 12
    age_now    = (now_dt.year - int(p["birth_year"])) + (now_dt.month - int(p["birth_month"])) / 12
    yrs_to_ret = max(0.0, int(p["retirement_age"]) - age_at_sav)
    st.info(f"📅 Age at savings date: **{age_at_sav:.1f}** | Current age: **{age_now:.1f}** | Years to retirement: **{yrs_to_ret:.1f}**")
    save_widget("profile")


# ── Tab: Contributions ────────────────────────────────────────────────────────
with tabs[2]:
    st.header("Contributions")
    c = data["contributions"]
    col1, col2 = st.columns(2)
    with col1:
        c["monthly_contribution"] = st.number_input("Monthly Contribution ($)", 0.0, value=float(c["monthly_contribution"]), step=50.0, format="%.2f")
        c["yearly_contribution"]  = st.number_input("Additional Yearly Contribution ($)", 0.0, value=float(c.get("yearly_contribution", 0.0)), step=500.0, format="%.2f")
    with col2:
        c["yearly_contribution_month"] = st.number_input("Month of Yearly Contribution (1–12)", 1, 12, int(c.get("yearly_contribution_month", 1)), 1)
        c["contribution_growth_rate"]  = st.number_input("Annual Contribution Growth Rate (%)", 0.0, 20.0, float(c["contribution_growth_rate"]), 0.5, format="%.2f")
    total_annual = float(c["monthly_contribution"]) * 12 + float(c.get("yearly_contribution", 0.0))
    st.info(f"💵 Total annual contribution (today): **${total_annual:,.0f}**")
    save_widget("contrib")


# ── Tab: Growth ───────────────────────────────────────────────────────────────
with tabs[3]:
    st.header("Investment Growth")
    g = data["growth"]
    g["annual_interest_rate"] = st.number_input("Expected Annual Return (%)", 0.0, 30.0, float(g["annual_interest_rate"]), 0.25, format="%.2f")
    if g["annual_interest_rate"] > 0:
        st.info(f"📐 Rule of 72: money doubles every **{72 / g['annual_interest_rate']:.1f} years**.")
    save_widget("growth")


# ── Tab: Lump Sums ────────────────────────────────────────────────────────────
with tabs[4]:
    st.header("Lump Sum Injections")
    lump_sums = data["lump_sums"]
    with st.expander("➕ Add New Lump Sum", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        ls_year   = c1.number_input("Year",  2000, 2080, datetime.now().year + 1, 1, key="ls_y")
        ls_month  = c2.number_input("Month", 1, 12, 1, 1, key="ls_m")
        ls_amount = c3.number_input("Amount ($)", 0.0, value=10000.0, step=1000.0, key="ls_a")
        ls_label  = c4.text_input("Label", value="Bonus", key="ls_l")
        if st.button("➕ Add Lump Sum"):
            lump_sums.append({"year": ls_year, "month": ls_month, "amount": ls_amount, "label": ls_label})
            save_user_data(current_user, data, note=f"Added lump sum: {ls_label} ${ls_amount:,.0f} in {ls_year}-{ls_month:02d}")
            st.rerun()
    if lump_sums:
        for i, ls in enumerate(lump_sums):
            c1, c2, c3, c4 = st.columns([2, 2, 4, 1])
            c1.write(f"📅 {ls['year']}-{int(ls['month']):02d}")
            c2.write(f"**${float(ls['amount']):,.0f}**")
            c3.write(ls.get("label", ""))
            if c4.button("🗑️", key=f"del_ls_{i}"):
                removed = lump_sums.pop(i)
                save_user_data(current_user, data, note=f"Removed lump sum: {removed.get('label','')} in {removed['year']}-{int(removed['month']):02d}")
                st.rerun()
    else:
        st.info("No lump sums added yet.")


# ── Tab: Retirement ───────────────────────────────────────────────────────────
with tabs[5]:
    st.header("Retirement Settings")
    r = data["retirement"]
    st.subheader("Withdrawal")
    wc1, wc2, wc3, wc4 = st.columns(4)
    r["monthly_withdrawal"]              = wc1.number_input("Monthly Withdrawal ($)", 0.0, value=float(r["monthly_withdrawal"]), step=100.0, format="%.2f")
    r["withdrawal_base_year"]            = wc2.number_input("PV Base Year", 2000, 2080, int(r.get("withdrawal_base_year", datetime.now().year)), 1)
    r["inflation_rate"]                  = wc3.number_input("Inflation Rate (%)", 0.0, 20.0, float(r["inflation_rate"]), 0.25, format="%.2f")
    r["adjust_withdrawal_for_inflation"] = wc4.checkbox("Adjust for Inflation", value=r.get("adjust_withdrawal_for_inflation", True))

    p2 = data["profile"]
    sav_yr2  = int(p2.get("savings_year",  datetime.now().year))
    sav_mo2  = int(p2.get("savings_month", datetime.now().month))
    age_sav2 = (sav_yr2 - int(p2["birth_year"])) + (sav_mo2 - int(p2["birth_month"])) / 12
    ytr2     = max(0.0, int(p2["retirement_age"]) - age_sav2)
    if r["adjust_withdrawal_for_inflation"] and r["inflation_rate"] > 0:
        mb = months_between(int(r["withdrawal_base_year"]), 1, sav_yr2, sav_mo2) + int(ytr2 * 12)
        w_at_ret = float(r["monthly_withdrawal"]) * ((1 + r["inflation_rate"] / 100 / 12) ** mb)
        st.info(f"💡 At retirement, this withdrawal ≈ **${w_at_ret:,.0f}/mo** nominal "
                f"(compounded from {int(r['withdrawal_base_year'])} at {r['inflation_rate']}% inflation).")

    st.divider()
    st.subheader("💰 Retirement Income Streams")
    st.caption("Each amount is in present-value dollars as of the PV Base Year. If *Inflation Adj.* is on, the payout grows with inflation from that year.")
    income_streams = data.setdefault("income_streams", [])

    with st.expander("➕ Add Income Stream", expanded=len(income_streams) == 0):
        na1, na2, na3, na4, na5, na6 = st.columns([3, 2, 2, 2, 2, 1])
        new_label  = na1.text_input("Name",           value="Social Security", key="ns_label")
        new_amount = na2.number_input("Monthly ($)",  0.0, value=1000.0, step=50.0, key="ns_amount")
        new_base   = na3.number_input("PV Base Year", 2000, 2080, datetime.now().year, 1, key="ns_base")
        new_age    = na4.number_input("Start Age",    50, 100, 67, 1, key="ns_age")
        new_inf    = na5.checkbox("Inflation Adj.", value=True, key="ns_inf")
        if na6.button("➕ Add"):
            income_streams.append({"label": new_label, "monthly_amount": float(new_amount),
                                   "base_year": int(new_base), "start_age": int(new_age),
                                   "adjust_for_inflation": bool(new_inf)})
            save_user_data(current_user, data, note=f"Added income stream: {new_label} ${new_amount:,.0f}/mo from age {new_age}")
            st.rerun()

    if income_streams:
        inf_rate = float(r["inflation_rate"])
        now_is   = datetime.now()
        age_now_is = (now_is.year - int(data["profile"]["birth_year"])) + (now_is.month - int(data["profile"]["birth_month"])) / 12
        hcols = st.columns([3, 2, 2, 2, 2, 2, 1])
        for hc, hl in zip(hcols, ["Name", "Monthly (PV $)", "PV Base Year", "Start Age", "Est. at Start", "Inflation Adj.", ""]):
            hc.markdown(f"**{hl}**")
        st.markdown("---")
        for i, s in enumerate(income_streams):
            yts  = max(0.0, float(s["start_age"]) - age_now_is)
            byr  = int(s.get("base_year", now_is.year))
            bamt = float(s["monthly_amount"])
            if s.get("adjust_for_inflation", True) and inf_rate > 0:
                mbs = months_between(byr, 1, now_is.year, now_is.month) + int(yts * 12)
                at_start_str = f"~${bamt * ((1 + inf_rate/100/12)**mbs):,.0f}/mo"
            else:
                at_start_str = f"${bamt:,.0f}/mo (fixed)"
            ec = st.columns([3, 2, 2, 2, 2, 2, 1])
            s["label"]          = ec[0].text_input("Name", value=s["label"], key=f"sl_{i}", label_visibility="collapsed")
            s["monthly_amount"] = ec[1].number_input("Amt", 0.0, value=bamt, step=50.0, format="%.2f", key=f"sa_{i}", label_visibility="collapsed")
            s["base_year"]      = ec[2].number_input("By",  2000, 2080, byr, 1, key=f"sb_{i}", label_visibility="collapsed")
            s["start_age"]      = ec[3].number_input("Sa",  50, 100, int(s["start_age"]), 1, key=f"ss_{i}", label_visibility="collapsed")
            ec[4].markdown(at_start_str)
            s["adjust_for_inflation"] = ec[5].checkbox("Adj.", value=bool(s.get("adjust_for_inflation", True)), key=f"si_{i}", label_visibility="collapsed")
            if ec[6].button("🗑️", key=f"del_is_{i}"):
                removed = income_streams.pop(i)
                save_user_data(current_user, data, note=f"Removed income stream: {removed['label']}")
                st.rerun()
        st.info(f"📊 **{len(income_streams)}** stream(s) · combined PV: **${sum(float(s['monthly_amount']) for s in income_streams):,.0f}/mo**")
    else:
        st.info("No income streams yet.")
    st.divider()

    # ── Planned Retirement Withdrawal Events ──────────────────────────────────
    st.subheader("💸 Planned Withdrawal Events")
    st.caption(
        "Schedule one-time or periodic lump withdrawals from your portfolio — "
        "new car, travel, home renovation, etc. Amount is in present-value dollars "
        "as of the PV base year. These are deducted from the balance on top of your "
        "regular monthly withdrawal."
    )
    ret_withdrawals = data.setdefault("retirement_withdrawals", [])

    with st.expander("➕ Add Withdrawal Event", expanded=len(ret_withdrawals) == 0):
        rw_type = st.radio("Type", ["One-time", "Periodic"], horizontal=True, key="rw_type")
        ra1, ra2, ra3, ra4, ra5 = st.columns([3, 2, 2, 2, 2])
        rw_label  = ra1.text_input("Label", value="New Car", key="rw_label")
        rw_amount = ra2.number_input("Amount ($, PV)", 0.0, value=10000.0, step=500.0, key="rw_amount")
        rw_base   = ra3.number_input("PV Base Year", 2000, 2080, datetime.now().year, 1, key="rw_base")
        rw_inf    = ra4.checkbox("Inflation Adj.", value=True, key="rw_inf",
                                 help="Compound the amount from PV base year to the withdrawal date")

        if rw_type == "One-time":
            rw_at_age = ra5.number_input("At Age", 40, 110, 70, 1, key="rw_at_age")
            if st.button("➕ Add One-time Withdrawal"):
                ret_withdrawals.append({
                    "type": "one_time", "label": rw_label,
                    "amount": float(rw_amount), "base_year": int(rw_base),
                    "adjust_for_inflation": bool(rw_inf),
                    "at_age": int(rw_at_age),
                })
                save_user_data(current_user, data,
                               note=f"Added withdrawal event: {rw_label} ${rw_amount:,.0f} at age {rw_at_age}")
                st.rerun()
        else:  # Periodic
            rb1, rb2, rb3 = st.columns(3)
            rw_from   = rb1.number_input("From Age", 40, 110, 60, 1, key="rw_from")
            rw_to     = rb2.number_input("To Age",   40, 110, 75, 1, key="rw_to")
            rw_every  = rb3.number_input("Every N Years", 1, 20, 2, 1, key="rw_every")
            if st.button("➕ Add Periodic Withdrawal"):
                if rw_from > rw_to:
                    st.error("From Age must be ≤ To Age.")
                else:
                    # Preview which ages will fire
                    ages = []
                    a = float(rw_from)
                    while a <= rw_to + 1e-9:
                        ages.append(int(a))
                        a += rw_every
                    ret_withdrawals.append({
                        "type": "periodic", "label": rw_label,
                        "amount": float(rw_amount), "base_year": int(rw_base),
                        "adjust_for_inflation": bool(rw_inf),
                        "from_age": int(rw_from), "to_age": int(rw_to),
                        "every_n_years": int(rw_every),
                    })
                    save_user_data(current_user, data,
                                   note=f"Added periodic withdrawal: {rw_label} ${rw_amount:,.0f} every {rw_every}yr ages {rw_from}–{rw_to}")
                    st.rerun()

    if ret_withdrawals:
        inf_rate_rw = float(r["inflation_rate"])
        now_rw = datetime.now()
        p_rw   = data["profile"]
        age_now_rw = (now_rw.year - int(p_rw["birth_year"])) + (now_rw.month - int(p_rw["birth_month"])) / 12

        hc = st.columns([3, 2, 2, 2, 4, 2, 1])
        for col, lbl in zip(hc, ["Label", "Amount (PV)", "Base Year", "Inflation Adj.", "Schedule", "Est. Amount", ""]):
            col.markdown(f"**{lbl}**")
        st.markdown("---")

        for i, rw in enumerate(ret_withdrawals):
            base_amt = float(rw["amount"])
            adj      = bool(rw.get("adjust_for_inflation", True))
            byr      = int(rw.get("base_year", now_rw.year))

            # Build schedule text and pick a representative age for est. amount
            if rw["type"] == "one_time":
                sched_text  = f"Once at age **{rw['at_age']}**"
                rep_age     = float(rw["at_age"])
            else:
                ages_list = []
                a = float(rw["from_age"])
                while a <= rw["to_age"] + 1e-9:
                    ages_list.append(int(a))
                    a += rw["every_n_years"]
                sched_text = f"Every {rw['every_n_years']}yr · ages {rw['from_age']}–{rw['to_age']} → {ages_list}"
                rep_age    = float(rw["from_age"])

            if adj and inf_rate_rw > 0:
                yts = max(0.0, rep_age - age_now_rw)
                m_base_to_rep = months_between(byr, 1, now_rw.year, now_rw.month) + int(yts * 12)
                est_amt = base_amt * ((1 + inf_rate_rw / 100 / 12) ** m_base_to_rep)
                est_str = f"~${est_amt:,.0f}"
            else:
                est_str = f"${base_amt:,.0f} (fixed)"

            rc = st.columns([3, 2, 2, 2, 4, 2, 1])
            rc[0].write(f"**{rw['label']}**")
            rc[1].write(f"${base_amt:,.0f}")
            rc[2].write(str(byr))
            rc[3].write("✅ Yes" if adj else "❌ No")
            rc[4].markdown(sched_text)
            rc[5].write(est_str)
            if rc[6].button("🗑️", key=f"del_rw_{i}"):
                removed = ret_withdrawals.pop(i)
                save_user_data(current_user, data, note=f"Removed withdrawal event: {removed['label']}")
                st.rerun()

        total_events = sum(
            1 if rw["type"] == "one_time"
            else len([a for a in [float(rw["from_age"]) + j * float(rw["every_n_years"])
                                  for j in range(100)]
                      if a <= rw["to_age"] + 1e-9])
            for rw in ret_withdrawals
        )
        st.info(f"💸 **{len(ret_withdrawals)}** withdrawal rule(s) → **{total_events}** total event(s) scheduled")
    else:
        st.info("No planned withdrawal events yet.")

    st.divider()
    save_widget("retirement")


# ── Tab: Snapshots ────────────────────────────────────────────────────────────
with tabs[6]:
    st.header("📸 Actual Savings Snapshots")
    snapshots = data["snapshots"]
    with st.expander("➕ Add Snapshot", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        sn_year  = c1.number_input("Year",  2000, 2080, datetime.now().year,  1, key="sn_y")
        sn_month = c2.number_input("Month", 1, 12, datetime.now().month, 1, key="sn_m")
        sn_bal   = c3.number_input("Actual Balance ($)", 0.0, value=0.0, step=1000.0, key="sn_b")
        sn_label = c4.text_input("Label (optional)", value="", key="sn_l")
        if st.button("➕ Add Snapshot"):
            snapshots.append({"year": int(sn_year), "month": int(sn_month), "balance": float(sn_bal), "label": sn_label})
            snapshots.sort(key=lambda x: (x["year"], x["month"]))
            save_user_data(current_user, data, note=f"Added snapshot: ${sn_bal:,.0f} on {sn_year}-{sn_month:02d}")
            st.rerun()
    if snapshots:
        _df_snap = run_projection(data)
        for i, sn in enumerate(snapshots):
            yr, mo, actual = int(sn["year"]), int(sn["month"]), float(sn["balance"])
            label = sn.get("label", "")
            match = _df_snap[(_df_snap["year"] == yr) & (_df_snap["month"] == mo)]
            if not match.empty:
                projected = match.iloc[0]["balance"]
                diff = actual - projected
                pct  = (diff / projected * 100) if projected > 0 else 0.0
                status = (f"✅ Ahead by **${diff:,.0f}** ({pct:+.1f}%)" if diff >= 0 else f"⚠️ Behind by **${abs(diff):,.0f}** ({pct:+.1f}%)")
                color  = "success" if diff >= 0 else "warning"
            else:
                projected, status, color = None, "ℹ️ Outside projection range", "info"
            cols = st.columns([2, 2, 2, 3, 1])
            cols[0].write(f"📅 **{yr}-{mo:02d}**" + (f"  _{label}_" if label else ""))
            cols[1].write(f"Actual: **${actual:,.0f}**")
            cols[2].write(f"Projected: **${projected:,.0f}**" if projected else "—")
            getattr(cols[3], color)(status)
            if cols[4].button("🗑️", key=f"del_sn_{i}"):
                snapshots.pop(i)
                save_user_data(current_user, data, note=f"Removed snapshot {yr}-{mo:02d}")
                st.rerun()
    else:
        st.info("No snapshots recorded yet.")


# ── Tab: Change Log ───────────────────────────────────────────────────────────
with tabs[7]:
    st.header("📋 Change Log")
    changelog = data.get("changelog", [])
    if not changelog:
        st.info("No saved versions yet.")
    else:
        latest_v = changelog[-1]["version"]
        st.caption(f"**{len(changelog)} version(s)** · latest is **v{latest_v}**")
        for entry in reversed(changelog):
            v = entry["version"]; ts = entry["timestamp"].replace("T", " "); note = entry["note"]
            is_latest = (v == latest_v)
            with st.expander(f"{'🟢 ' if is_latest else ''}**v{v}** — {ts}   _{note[:80]}{'…' if len(note)>80 else ''}_", expanded=is_latest):
                st.markdown(f"**Note:** {note}")
                s = entry["settings"]
                prof = s.get("profile", {}); cont = s.get("contributions", {}); grow = s.get("growth", {}); ret = s.get("retirement", {}); iss = s.get("income_streams", [])
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Profile**  \nSavings: ${float(prof.get('current_savings',0)):,.0f}  \nRetire at: {prof.get('retirement_age','—')}  \nEnd age: {prof.get('end_age','—')}")
                c2.markdown(f"**Contributions**  \nMonthly: ${float(cont.get('monthly_contribution',0)):,.0f}  \nYearly: ${float(cont.get('yearly_contribution',0)):,.0f}  \nGrowth: {cont.get('contribution_growth_rate',0)}%")
                c3.markdown(f"**Growth & Withdrawal**  \nReturn: {grow.get('annual_interest_rate',0)}%  \nWithdrawal: ${float(ret.get('monthly_withdrawal',0)):,.0f}/mo  \nInflation: {ret.get('inflation_rate',0)}%")
                if iss:
                    st.markdown("**Income Streams**  \n" + "  \n".join(f"• {s2['label']}: ${float(s2['monthly_amount']):,.0f}/mo (PV {s2.get('base_year','?')}) · age {s2['start_age']} · {'infl. adj.' if s2.get('adjust_for_inflation') else 'fixed'}" for s2 in iss))
                prev_entries = [e for e in changelog if e["version"] == v - 1]
                if prev_entries:
                    diff_text = _diff_summary(prev_entries[0]["settings"], s)
                    if diff_text and diff_text != "No field changes detected":
                        st.markdown(f"**Changes from v{v-1}:** `{diff_text}`")
                if not is_latest:
                    if st.button(f"↩️ Restore v{v}", key=f"restore_{v}"):
                        restore_version(current_user, data, v)
        st.divider()
        with st.expander("📤 Export Change Log as CSV"):
            rows = [{"version": e["version"], "timestamp": e["timestamp"], "note": e["note"],
                     "savings": e["settings"].get("profile", {}).get("current_savings", ""),
                     "retire_age": e["settings"].get("profile", {}).get("retirement_age", ""),
                     "monthly_contrib": e["settings"].get("contributions", {}).get("monthly_contribution", ""),
                     "return_rate": e["settings"].get("growth", {}).get("annual_interest_rate", ""),
                     "monthly_withdrawal": e["settings"].get("retirement", {}).get("monthly_withdrawal", ""),
                     "inflation": e["settings"].get("retirement", {}).get("inflation_rate", "")} for e in changelog]
            st.download_button("⬇️ Download CSV", data=pd.DataFrame(rows).to_csv(index=False),
                               file_name=f"changelog_{current_user}.csv", mime="text/csv")


# ── Tab: Account ──────────────────────────────────────────────────────────────
with tabs[8]:
    st.header("⚙️ Account Settings")

    # ── Change password ───────────────────────────────────────────────────────
    st.subheader("Change Password")
    with st.form("change_pw_form"):
        old_pw  = st.text_input("Current Password", type="password")
        new_pw  = st.text_input("New Password",     type="password")
        new_pw2 = st.text_input("Confirm New Password", type="password")
        if st.form_submit_button("Update Password"):
            if not verify_password(profiles, current_user, old_pw):
                st.error("Current password is incorrect.")
            elif new_pw != new_pw2:
                st.error("New passwords do not match.")
            else:
                err = change_password(profiles, current_user, new_pw)
                if err:
                    st.error(err)
                else:
                    save_profiles(profiles)
                    st.session_state.profiles_cache = profiles
                    st.success("Password updated successfully.")

    st.divider()

    # ── Admin section ─────────────────────────────────────────────────────────
    if is_admin:
        st.subheader("🔑 Admin — Manage Profiles")
        st.caption(f"You are the admin account. There are **{len(profiles['profiles'])}** profile(s).")

        for uname, uinfo in list(profiles["profiles"].items()):
            is_self = (uname == current_user)
            uc1, uc2, uc3 = st.columns([3, 3, 1])
            uc1.write(f"**{uname}**" + (" *(you)*" if is_self else ""))
            uc2.write(f"Created: {uinfo.get('created','—')[:10]}")
            if not is_self:
                if uc3.button("🗑️ Delete", key=f"del_user_{uname}"):
                    del profiles["profiles"][uname]
                    save_profiles(profiles)
                    st.session_state.profiles_cache = profiles
                    st.success(f"Profile **{uname}** deleted.")
                    st.rerun()
    else:
        st.info("Admin features are available to the first registered account only.")


# ── Tab: Dashboard ────────────────────────────────────────────────────────────
with tabs[0]:
    st.header("Dashboard")
    df = run_projection(data)

    if df.empty:
        st.warning("Please complete your profile settings first.")
    else:
        p_d  = data["profile"]; now = datetime.now()
        age_now_d  = (now.year - int(p_d["birth_year"])) + (now.month - int(p_d["birth_month"])) / 12
        sav_yr_d   = int(p_d.get("savings_year",  now.year))
        sav_mo_d   = int(p_d.get("savings_month", now.month))
        age_sav_d  = (sav_yr_d - int(p_d["birth_year"])) + (sav_mo_d - int(p_d["birth_month"])) / 12
        yrs_to_ret_d = max(0.0, int(p_d["retirement_age"]) - age_sav_d)
        curr = p_d.get("currency", "USD")

        balance_at_ret = df[df["is_retired"]]["balance"].iloc[0] if df["is_retired"].any() else df["balance"].iloc[-1]
        peak_balance   = df["balance"].max()
        final_balance  = df["balance"].iloc[-1]

        depleted = df[df["balance"] <= 0]
        if not depleted.empty:
            dep_row = depleted.iloc[0]
            st.error(f"⚠️ Savings depleted at age **{dep_row['age']:.1f}** ({int(dep_row['year'])})")
        else:
            st.success(f"✅ Savings last through age **{int(p_d.get('end_age', 95))}** — you're on track!")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Current Age",           f"{age_now_d:.1f}")
        m2.metric("Years to Retirement",   f"{yrs_to_ret_d:.1f}")
        m3.metric("Balance at Retirement", f"${balance_at_ret:,.0f}")
        m4.metric("Peak Balance",          f"${peak_balance:,.0f}")
        m5.metric("Balance at End",        f"${final_balance:,.0f}")

        snapshots = data.get("snapshots", [])
        if snapshots:
            for sn in reversed(snapshots):
                row = df[(df["year"] == int(sn["year"])) & (df["month"] == int(sn["month"]))]
                if not row.empty:
                    proj = row.iloc[0]["balance"]; actual = float(sn["balance"]); diff = actual - proj
                    pct  = (diff / proj * 100) if proj > 0 else 0.0
                    msg  = (f"{'✅ Ahead' if diff >= 0 else '⚠️ Behind'} as of latest snapshot "
                            f"({int(sn['year'])}-{int(sn['month']):02d}): actual **${actual:,.0f}** vs projected **${proj:,.0f}** "
                            f"({'▲' if diff >= 0 else '▼'} **${abs(diff):,.0f}** / {pct:+.1f}%)")
                    (st.success if diff >= 0 else st.warning)(msg)
                    break

        st.subheader("Portfolio Balance Over Time")
        df_yr = df.groupby("age_int").last().reset_index()
        pre = df_yr[~df_yr["is_retired"]]; post = df_yr[df_yr["is_retired"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pre["age_int"],  y=pre["balance"],  name="Accumulation",
            fill="tozeroy", line=dict(color="#1a73e8", width=2.5), fillcolor="rgba(26,115,232,0.12)"))
        fig.add_trace(go.Scatter(x=post["age_int"], y=post["balance"], name="Retirement",
            fill="tozeroy", line=dict(color="#f9a825", width=2.5), fillcolor="rgba(249,168,37,0.12)"))
        for ls in data["lump_sums"]:
            row = df[(df["year"] == ls["year"]) & (df["month"] == int(ls["month"]))]
            if not row.empty:
                r_row = row.iloc[0]
                fig.add_trace(go.Scatter(x=[r_row["age"]], y=[r_row["balance"]], mode="markers",
                    marker=dict(symbol="star", size=14, color="#9c27b0"), name=ls.get("label", "Lump Sum"),
                    hovertemplate=f"{ls.get('label','Lump Sum')}<br>${float(ls['amount']):,.0f}<extra></extra>"))

        # Planned retirement withdrawal event markers
        rw_ages, rw_bals, rw_texts = [], [], []
        for rw in data.get("retirement_withdrawals", []):
            if rw["type"] == "one_time":
                hit_ages = [float(rw["at_age"])]
            else:
                hit_ages, a = [], float(rw["from_age"])
                while a <= rw["to_age"] + 1e-9:
                    hit_ages.append(a); a += rw["every_n_years"]
            for hit_age in hit_ages:
                hit_row = df[(df["age"] >= hit_age - 0.1) & (df["age"] <= hit_age + 0.1)]
                if not hit_row.empty:
                    hr = hit_row.iloc[0]
                    rw_ages.append(hr["age"]); rw_bals.append(hr["balance"])
                    rw_texts.append(f"💸 {rw['label']}<br>Age {hr['age']:.1f}<extra></extra>")
        if rw_ages:
            fig.add_trace(go.Scatter(x=rw_ages, y=rw_bals, mode="markers",
                name="Planned Withdrawal",
                marker=dict(symbol="arrow-down", size=14, color="#e53935"),
                text=rw_texts, hovertemplate="%{text}"))
        if snapshots:
            snap_ages, snap_bals, snap_texts = [], [], []
            for sn in snapshots:
                row = df[(df["year"] == int(sn["year"])) & (df["month"] == int(sn["month"]))]
                if not row.empty:
                    age_v = row.iloc[0]["age"]; proj_v = row.iloc[0]["balance"]; act_v = float(sn["balance"])
                    diff = act_v - proj_v; pct = (diff / proj_v * 100) if proj_v > 0 else 0.0
                    snap_ages.append(age_v); snap_bals.append(act_v)
                    snap_texts.append(f"{sn.get('label','') + '<br>' if sn.get('label') else ''}Actual: ${act_v:,.0f}<br>Projected: ${proj_v:,.0f}<br>{'▲' if diff>=0 else '▼'} {pct:+.1f}%")
            if snap_ages:
                fig.add_trace(go.Scatter(x=snap_ages, y=snap_bals, mode="lines+markers",
                    name="Actual (Snapshots)", line=dict(color="#e53935", width=2.5),
                    marker=dict(size=11, symbol="circle", line=dict(color="white", width=1.5)),
                    text=snap_texts, hovertemplate="%{text}<extra></extra>"))
        fig.add_vline(x=int(p_d["retirement_age"]), line_dash="dot", line_color="gray",
                      annotation_text="Retirement", annotation_position="top right")
        fig.update_layout(xaxis_title="Age", yaxis_title=f"Balance ({curr})", hovermode="x unified",
                          height=420, margin=dict(t=40, b=40),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left"))
        st.plotly_chart(fig, use_container_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Annual Interest Earned")
            di = df.groupby("age_int")["interest"].sum().reset_index()
            fig2 = go.Figure(go.Bar(x=di["age_int"], y=di["interest"], marker_color="#43a047"))
            fig2.update_layout(xaxis_title="Age", yaxis_title=curr, height=300, margin=dict(t=20))
            st.plotly_chart(fig2, use_container_width=True)
        with col_b:
            st.subheader("Annual Contributions")
            dc = df.groupby("age_int")["contribution"].sum().reset_index()
            fig3 = go.Figure(go.Bar(x=dc["age_int"], y=dc["contribution"], marker_color="#1a73e8"))
            fig3.update_layout(xaxis_title="Age", yaxis_title=curr, height=300, margin=dict(t=20))
            st.plotly_chart(fig3, use_container_width=True)

        if df["is_retired"].any():
            st.subheader("Retirement Cash Flow (Annual)")
            df_ret = df[df["is_retired"]].groupby("age_int").agg(
                withdrawal=("withdrawal","sum"), income=("income","sum"), net_withdrawal=("net_withdrawal","sum")).reset_index()
            fig4 = go.Figure()
            fig4.add_trace(go.Bar(x=df_ret["age_int"], y=df_ret["withdrawal"], name="Gross Withdrawal", marker_color="#ef5350"))
            fig4.add_trace(go.Bar(x=df_ret["age_int"], y=df_ret["income"],     name=f"Income Streams ({len(data.get('income_streams',[]))})", marker_color="#43a047"))
            fig4.add_trace(go.Scatter(x=df_ret["age_int"], y=df_ret["net_withdrawal"], name="Net from Savings", line=dict(color="#f9a825", width=2, dash="dot")))
            fig4.update_layout(barmode="overlay", xaxis_title="Age", yaxis_title=curr, height=320, legend=dict(orientation="h"), margin=dict(t=20))
            st.plotly_chart(fig4, use_container_width=True)

        with st.expander("📋 Year-by-Year Table"):
            agg = df.groupby("age_int").agg(
                year=("year","last"), balance=("balance","last"),
                interest_annual=("interest","sum"), contrib_annual=("contribution","sum"),
                lump_annual=("lump_sum","sum"), withdrawal_annual=("withdrawal","sum"),
                income_annual=("income","sum"), net_wd_annual=("net_withdrawal","sum"),
                plan_wd_annual=("planned_withdrawal","sum"),
                month_count=("month","count")).reset_index()
            for col in ["interest","contrib","lump","withdrawal","income","net_wd","plan_wd"]:
                agg[f"{col}_mo"] = agg[f"{col}_annual"] / agg["month_count"]
            tbl_rows = [{"Age": int(row["age_int"]), "Year": int(row["year"]), "End Balance": f"${row['balance']:,.0f}",
                         "Interest (Annual)": f"${row['interest_annual']:,.0f}", "Interest (Monthly)": f"${row['interest_mo']:,.0f}",
                         "Contributions (Annual)": f"${row['contrib_annual']:,.0f}", "Contributions (Monthly)": f"${row['contrib_mo']:,.0f}",
                         "Lump Sums (Annual)": f"${row['lump_annual']:,.0f}",
                         "Withdrawal (Annual)": f"${row['withdrawal_annual']:,.0f}", "Withdrawal (Monthly)": f"${row['withdrawal_mo']:,.0f}",
                         "Income (Annual)": f"${row['income_annual']:,.0f}", "Income (Monthly)": f"${row['income_mo']:,.0f}",
                         "Net W/D (Annual)": f"${row['net_wd_annual']:,.0f}", "Net W/D (Monthly)": f"${row['net_wd_mo']:,.0f}",
                         "Planned W/D Events": f"${row['plan_wd_annual']:,.0f}"} for _, row in agg.iterrows()]
            tbl_df = pd.DataFrame(tbl_rows)
            view_mode = st.radio("Show", ["Annual Totals", "Monthly Averages", "Both"], horizontal=True, key="tbl_view")
            annual_cols  = ["Age","Year","End Balance","Interest (Annual)","Contributions (Annual)","Lump Sums (Annual)","Withdrawal (Annual)","Income (Annual)","Net W/D (Annual)","Planned W/D Events"]
            monthly_cols = ["Age","Year","End Balance","Interest (Monthly)","Contributions (Monthly)","Withdrawal (Monthly)","Income (Monthly)","Net W/D (Monthly)","Planned W/D Events"]
            show_cols = annual_cols if view_mode=="Annual Totals" else (monthly_cols if view_mode=="Monthly Averages" else list(dict.fromkeys(annual_cols+monthly_cols)))
            st.dataframe(tbl_df[show_cols], use_container_width=True, hide_index=True)

        with st.expander("🔬 Sensitivity: Balance at Retirement vs. Return Rate"):
            rates, balances = [3,4,5,6,7,8,9,10], []
            orig = data["growth"]["annual_interest_rate"]
            for rate in rates:
                data["growth"]["annual_interest_rate"] = rate
                tmp = run_projection(data)
                bal = tmp[tmp["is_retired"]]["balance"].iloc[0] if tmp["is_retired"].any() else tmp["balance"].iloc[-1]
                balances.append(bal)
            data["growth"]["annual_interest_rate"] = orig
            fig5 = go.Figure(go.Scatter(x=rates, y=balances, mode="lines+markers", line=dict(color="#1a73e8", width=2.5), marker=dict(size=8)))
            fig5.update_layout(xaxis_title="Annual Return Rate (%)", yaxis_title=f"Balance at Retirement ({curr})", height=300, margin=dict(t=20))
            st.plotly_chart(fig5, use_container_width=True)
