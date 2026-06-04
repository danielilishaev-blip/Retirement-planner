"""
Personal Retirement Planner  –  with Change Log & Versioning
Run with:  streamlit run retirement_planner.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 REVISION HISTORY  (last 10 revisions, most recent first)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 V1.2  2026-06-04  Cloud deployment support – added dual storage
                   backend: when GITHUB_TOKEN / GITHUB_REPO secrets
                   are present (Streamlit Cloud) data is read/written
                   to a JSON file in the GitHub repo via the GitHub
                   API; otherwise falls back to local file as before.
                   Added requests + base64 imports. Same code base
                   runs locally and on Streamlit Cloud unchanged.

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
import json
import os
import requests
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Persistence ───────────────────────────────────────────────────────────────
# Auto-detects local vs Streamlit Cloud:
#   • Local:  reads/writes retirement_data.json next to this script.
#   • Cloud:  reads/writes the same JSON file inside your GitHub repo
#             via the GitHub API, using secrets defined in Streamlit Cloud.
#
# Required Streamlit secrets for cloud mode (Settings → Secrets):
#   GITHUB_TOKEN     = "ghp_xxxxxxxxxxxx"   # Personal Access Token (repo scope)
#   GITHUB_REPO      = "your-username/your-repo-name"
#   GITHUB_DATA_FILE = "retirement_data.json"   # optional, this is the default

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retirement_data.json")


def _is_cloud() -> bool:
    """Return True when GitHub secrets are configured (running on Streamlit Cloud)."""
    try:
        return bool(st.secrets.get("GITHUB_TOKEN"))
    except Exception:
        return False


def _gh_headers() -> dict:
    return {
        "Authorization": f"token {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github.v3+json",
    }


def _gh_url() -> str:
    repo = st.secrets["GITHUB_REPO"]
    path = st.secrets.get("GITHUB_DATA_FILE", "retirement_data.json")
    return f"https://api.github.com/repos/{repo}/contents/{path}"


def _github_load() -> dict | None:
    """Fetch JSON data from GitHub repo. Stores the file SHA for subsequent saves."""
    try:
        r = requests.get(_gh_url(), headers=_gh_headers(), timeout=10)
        if r.status_code == 200:
            body = r.json()
            raw  = base64.b64decode(body["content"]).decode()
            st.session_state["_gh_sha"] = body["sha"]
            return json.loads(raw)
        if r.status_code == 404:
            return None   # file doesn't exist yet – will be created on first save
        st.warning(f"GitHub load returned {r.status_code}")
    except Exception as e:
        st.warning(f"Could not load from GitHub: {e}")
    return None


def _github_save(data: dict) -> bool:
    """Write JSON data back to GitHub repo (create or update)."""
    try:
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        payload: dict = {
            "message": f"retirement-planner save {datetime.now().isoformat(timespec='seconds')}",
            "content": content,
        }
        sha = st.session_state.get("_gh_sha")
        if sha:
            payload["sha"] = sha      # required for updating an existing file
        r = requests.put(_gh_url(), headers=_gh_headers(), json=payload, timeout=15)
        if r.status_code in (200, 201):
            st.session_state["_gh_sha"] = r.json()["content"]["sha"]
            return True
        st.error(f"GitHub save failed ({r.status_code}): {r.text[:300]}")
    except Exception as e:
        st.error(f"GitHub save error: {e}")
    return False

SETTINGS_KEYS = ["profile", "contributions", "growth", "lump_sums", "retirement",
                 "income_streams", "snapshots"]


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
    for label, key in [("Lump Sums", "lump_sums"), ("Income Streams", "income_streams"), ("Snapshots", "snapshots")]:
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
        "growth": {
            "annual_interest_rate": 7.0,
        },
        "lump_sums": [],
        "retirement": {
            "monthly_withdrawal": 3000.0,
            "withdrawal_base_year": now.year,
            "inflation_rate": 2.5,
            "adjust_withdrawal_for_inflation": True,
        },
        # Each income stream:
        # { label, monthly_amount, base_year, start_age, adjust_for_inflation }
        # monthly_amount is the PV as of base_year.
        # If adjust_for_inflation=True, the amount is compounded from base_year
        # to the payment month at the inflation rate.
        # If False, the amount is paid as-is (nominal/fixed).
        "income_streams": [],
        "snapshots": [],
        "changelog": [],
    }


def _migrate(data: dict) -> dict:
    dd = default_data()
    for k, v in dd.items():
        if k not in data:
            data[k] = v

    now = datetime.now()

    # Add savings_year / savings_month to profile if missing (old saves)
    prof = data.get("profile", {})
    if "savings_year" not in prof:
        prof["savings_year"] = now.year
    if "savings_month" not in prof:
        prof["savings_month"] = now.month

    r = data.get("retirement", {})

    # Add withdrawal base year if missing
    if "withdrawal_base_year" not in r:
        r["withdrawal_base_year"] = now.year

    # Migrate old SS + other_income → income_streams
    old_ss      = r.pop("social_security_monthly", None)
    old_ss_age  = r.pop("social_security_start_age", None)
    old_other   = r.pop("other_income_monthly", None)

    streams = data.setdefault("income_streams", [])
    existing_labels = {s["label"] for s in streams}

    if old_ss is not None and float(old_ss) > 0 and "Social Security" not in existing_labels:
        streams.append({
            "label": "Social Security",
            "monthly_amount": float(old_ss),
            "base_year": now.year,
            "start_age": int(old_ss_age) if old_ss_age else 67,
            "adjust_for_inflation": True,
        })
    if old_other is not None and float(old_other) > 0 and "Other Income" not in existing_labels:
        streams.append({
            "label": "Other Income",
            "monthly_amount": float(old_other),
            "base_year": now.year,
            "start_age": int(data["profile"].get("retirement_age", 65)),
            "adjust_for_inflation": False,
        })

    # Add base_year to existing streams that lack it
    for s in streams:
        if "base_year" not in s:
            s["base_year"] = now.year

    return data


def load_data() -> dict:
    if _is_cloud():
        loaded = _github_load()
        if loaded:
            return _migrate(loaded)
        return default_data()
    # Local mode
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, "r") as f:
            loaded = json.load(f)
        return _migrate(loaded)
    return default_data()


def save_data(data: dict, note: str = "") -> None:
    changelog = data.setdefault("changelog", [])
    prev_settings = changelog[-1]["settings"] if changelog else {}
    curr_settings = _settings_copy(data)
    auto_note  = _diff_summary(prev_settings, curr_settings) if prev_settings else "Initial save"
    final_note = note.strip() if note.strip() else auto_note
    version_num = (changelog[-1]["version"] + 1) if changelog else 1
    changelog.append({
        "version": version_num,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": final_note,
        "settings": curr_settings,
    })
    if _is_cloud():
        ok = _github_save(data)
        if ok:
            st.toast(f"✅ Saved as v{version_num} → GitHub", icon="☁️")
    else:
        with open(SAVE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        st.toast(f"✅ Saved as v{version_num}", icon="💾")


def restore_version(data: dict, version_num: int) -> None:
    for entry in data["changelog"]:
        if entry["version"] == version_num:
            for k in SETTINGS_KEYS:
                if k in entry["settings"]:
                    data[k] = copy.deepcopy(entry["settings"][k])
            save_data(data, note=f"Restored from v{version_num}")
            st.rerun()
            return
    st.error(f"Version {version_num} not found.")


# ── Projection Engine ─────────────────────────────────────────────────────────

def add_months(year: int, month: int, n: int):
    month += n
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def months_between(from_year: int, from_month: int, to_year: int, to_month: int) -> int:
    """Signed number of months from (from_year, from_month) to (to_year, to_month)."""
    return (to_year - from_year) * 12 + (to_month - from_month)


def run_projection(data: dict) -> pd.DataFrame:
    p  = data["profile"]
    c  = data["contributions"]
    g  = data["growth"]
    r  = data["retirement"]
    lump_sums      = data["lump_sums"]
    income_streams = data.get("income_streams", [])

    birth_year     = int(p["birth_year"])
    birth_month    = int(p["birth_month"])
    retirement_age = int(p["retirement_age"])
    end_age        = int(p.get("end_age", 95))

    now = datetime.now()
    current_year, current_month = now.year, now.month

    # Use the recorded savings date as the projection start point.
    # If savings_year/month is in the future, fall back to current month.
    savings_year  = int(p.get("savings_year",  current_year))
    savings_month = int(p.get("savings_month", current_month))
    if (savings_year, savings_month) > (current_year, current_month):
        savings_year, savings_month = current_year, current_month

    # All offsets are measured from the savings date
    start_year, start_month = savings_year, savings_month

    current_age_months      = (start_year - birth_year) * 12 + (start_month - birth_month)
    retirement_month_offset = retirement_age * 12 - current_age_months
    end_month_offset        = end_age * 12 - current_age_months

    if end_month_offset <= 0:
        return pd.DataFrame()

    lump_lookup: dict = {}
    for ls in lump_sums:
        key = (int(ls["year"]), int(ls["month"]))
        lump_lookup[key] = lump_lookup.get(key, 0) + float(ls["amount"])

    monthly_rate           = g["annual_interest_rate"] / 100 / 12
    inflation_monthly      = r["inflation_rate"] / 100 / 12
    contrib_growth_monthly = c["contribution_growth_rate"] / 100 / 12
    yearly_contrib_month   = int(c.get("yearly_contribution_month", 1))

    # Withdrawal base: months from withdrawal_base_year/Jan to savings start month
    w_base_year   = int(r.get("withdrawal_base_year", start_year))
    w_base_offset = months_between(w_base_year, 1, start_year, start_month)

    # Pre-compute income stream start offsets and PV offsets
    stream_data = []
    for s in income_streams:
        start_months_offset = int(float(s["start_age"]) * 12 - current_age_months)
        # months from stream's base_year/Jan to savings start
        pv_offset = months_between(int(s.get("base_year", start_year)), 1, start_year, start_month)
        stream_data.append({
            "monthly_amount": float(s["monthly_amount"]),
            "start_months_offset": start_months_offset,
            "adjust_for_inflation": bool(s.get("adjust_for_inflation", True)),
            "pv_offset": pv_offset,
        })

    balance              = float(p["current_savings"])
    base_monthly_contrib = float(c["monthly_contribution"])
    base_withdrawal      = float(r["monthly_withdrawal"])

    records = []
    for m in range(0, end_month_offset + 1):
        yr, mo     = add_months(start_year, start_month, m)
        age_months = current_age_months + m
        age        = age_months / 12
        is_retired = m >= retirement_month_offset

        interest = balance * monthly_rate
        balance += interest

        contrib = 0.0
        lump    = lump_lookup.get((yr, mo), 0.0)

        if not is_retired:
            gf = (1 + contrib_growth_monthly) ** m
            contrib = base_monthly_contrib * gf
            if mo == yearly_contrib_month:
                contrib += float(c.get("yearly_contribution", 0)) * gf
            balance += contrib

        balance += lump

        withdrawal = income = net_withdrawal = 0.0
        if is_retired:
            months_retired = m - retirement_month_offset

            # Withdrawal: PV is as-of withdrawal_base_year, compound from there
            total_months_from_w_base = w_base_offset + m  # months since base_year/Jan to this month
            if r.get("adjust_withdrawal_for_inflation", True):
                withdrawal = base_withdrawal * ((1 + inflation_monthly) ** total_months_from_w_base)
            else:
                withdrawal = base_withdrawal

            # Sum income streams
            for sd in stream_data:
                if m >= sd["start_months_offset"]:
                    base_amt = sd["monthly_amount"]
                    if sd["adjust_for_inflation"]:
                        # Compound from the stream's base_year/Jan to this month
                        total_months_from_pv_base = sd["pv_offset"] + m
                        income += base_amt * ((1 + inflation_monthly) ** total_months_from_pv_base)
                    else:
                        income += base_amt

            net_withdrawal = max(0.0, withdrawal - income)
            balance = max(0.0, balance - net_withdrawal)

        records.append({
            "month_offset": m, "year": yr, "month": mo,
            "age": round(age, 2), "age_int": int(age),
            "balance": round(balance, 2), "interest": round(interest, 2),
            "contribution": round(contrib, 2), "lump_sum": round(lump, 2),
            "withdrawal": round(withdrawal, 2), "income": round(income, 2),
            "net_withdrawal": round(net_withdrawal, 2), "is_retired": is_retired,
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
</style>
""", unsafe_allow_html=True)

if "data" not in st.session_state:
    st.session_state.data = load_data()

data = st.session_state.data

st.title("💰 Personal Retirement Planner")

tabs = st.tabs([
    "📊 Dashboard",
    "👤 Profile",
    "💵 Contributions",
    "📈 Growth",
    "🎯 Lump Sums",
    "🏖️ Retirement",
    "📸 Snapshots",
    "📋 Change Log",
])


def save_widget(key: str) -> None:
    with st.container():
        note_col, btn_col = st.columns([4, 1])
        note = note_col.text_input(
            "Change note (optional)", value="", key=f"note_{key}",
            placeholder="e.g. Increased monthly contribution after raise",
        )
        if btn_col.button("💾 Save", key=f"save_{key}"):
            save_data(data, note=note)


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
        st.caption("Enter the balance and the exact year/month it was recorded. The projection starts from this date.")
        p["current_savings"] = st.number_input(
            "Initial Total Savings ($)", 0.0,
            value=float(p["current_savings"]), step=1000.0, format="%.2f",
        )
        sv_col1, sv_col2 = st.columns(2)
        p["savings_year"]  = sv_col1.number_input(
            "Savings Year", min_value=2000, max_value=2080,
            value=int(p.get("savings_year", datetime.now().year)), step=1,
        )
        p["savings_month"] = sv_col2.number_input(
            "Savings Month (1–12)", min_value=1, max_value=12,
            value=int(p.get("savings_month", datetime.now().month)), step=1,
        )

    with col2:
        p["retirement_age"] = st.number_input("Planned Retirement Age", 40, 90, int(p["retirement_age"]), 1)
        p["end_age"]        = st.number_input("Projection End Age", 65, 120, int(p.get("end_age", 95)), 1)
        currencies = ["USD", "EUR", "GBP", "ILS", "CAD", "AUD", "JPY", "CHF"]
        curr_sel = p.get("currency", "USD")
        p["currency"] = st.selectbox("Currency", currencies, index=currencies.index(curr_sel) if curr_sel in currencies else 0)

    now_dt = datetime.now()
    age_at_savings = (int(p["savings_year"]) - int(p["birth_year"])) + \
                     (int(p["savings_month"]) - int(p["birth_month"])) / 12
    age_now    = (now_dt.year - int(p["birth_year"])) + (now_dt.month - int(p["birth_month"])) / 12
    yrs_to_ret = max(0.0, int(p["retirement_age"]) - age_at_savings)
    st.info(
        f"📅 Age at savings date: **{age_at_savings:.1f}** | "
        f"Current age: **{age_now:.1f}** | "
        f"Years to retirement (from savings date): **{yrs_to_ret:.1f}**"
    )
    save_widget("profile")


# ── Tab: Contributions ────────────────────────────────────────────────────────
with tabs[2]:
    st.header("Contributions")
    c = data["contributions"]
    col1, col2 = st.columns(2)
    with col1:
        c["monthly_contribution"]  = st.number_input("Monthly Contribution ($)", 0.0, value=float(c["monthly_contribution"]), step=50.0, format="%.2f")
        c["yearly_contribution"]   = st.number_input("Additional Yearly Contribution ($)", 0.0, value=float(c.get("yearly_contribution", 0.0)), step=500.0, format="%.2f")
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
    g["annual_interest_rate"] = st.number_input(
        "Expected Annual Return (%)", 0.0, 30.0, float(g["annual_interest_rate"]), 0.25, format="%.2f",
    )
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
            save_data(data, note=f"Added lump sum: {ls_label} ${ls_amount:,.0f} in {ls_year}-{ls_month:02d}")
            st.rerun()

    if lump_sums:
        st.subheader("Scheduled")
        for i, ls in enumerate(lump_sums):
            c1, c2, c3, c4 = st.columns([2, 2, 4, 1])
            c1.write(f"📅 {ls['year']}-{int(ls['month']):02d}")
            c2.write(f"**${float(ls['amount']):,.0f}**")
            c3.write(ls.get("label", ""))
            if c4.button("🗑️", key=f"del_ls_{i}"):
                removed = lump_sums.pop(i)
                save_data(data, note=f"Removed lump sum: {removed.get('label','')} in {removed['year']}-{int(removed['month']):02d}")
                st.rerun()
    else:
        st.info("No lump sums added yet.")


# ── Tab: Retirement ───────────────────────────────────────────────────────────
with tabs[5]:
    st.header("Retirement Settings")
    r = data["retirement"]

    # ── Withdrawal ────────────────────────────────────────────────────────────
    st.subheader("Withdrawal")
    st.caption(
        "Enter your desired monthly withdrawal in **present value** dollars, "
        "and the year that value was set. The projection will compound it forward "
        "from that base year to each payment date."
    )
    wc1, wc2, wc3, wc4 = st.columns(4)
    r["monthly_withdrawal"] = wc1.number_input(
        "Monthly Withdrawal ($)", 0.0, value=float(r["monthly_withdrawal"]), step=100.0, format="%.2f",
    )
    r["withdrawal_base_year"] = wc2.number_input(
        "PV Base Year", min_value=2000, max_value=2080,
        value=int(r.get("withdrawal_base_year", datetime.now().year)), step=1,
        help="The year your withdrawal amount is expressed in (present value reference year).",
    )
    r["inflation_rate"] = wc3.number_input(
        "Inflation Rate (%)", 0.0, 20.0, float(r["inflation_rate"]), 0.25, format="%.2f",
    )
    r["adjust_withdrawal_for_inflation"] = wc4.checkbox(
        "Adjust for Inflation",
        value=r.get("adjust_withdrawal_for_inflation", True),
        help="Grow the withdrawal from the PV base year forward at the inflation rate.",
    )

    # Show estimated withdrawal at retirement
    now_dt2 = datetime.now()
    p2 = data["profile"]
    sav_yr2 = int(p2.get("savings_year",  now_dt2.year))
    sav_mo2 = int(p2.get("savings_month", now_dt2.month))
    age_at_sav2 = (sav_yr2 - int(p2["birth_year"])) + (sav_mo2 - int(p2["birth_month"])) / 12
    yrs_to_ret2  = max(0.0, int(p2["retirement_age"]) - age_at_sav2)
    if r["adjust_withdrawal_for_inflation"] and r["inflation_rate"] > 0:
        months_base_to_ret = months_between(
            int(r["withdrawal_base_year"]), 1,
            now_dt2.year, now_dt2.month,
        ) + int(yrs_to_ret2 * 12)
        w_at_ret = float(r["monthly_withdrawal"]) * ((1 + r["inflation_rate"] / 100 / 12) ** months_base_to_ret)
        st.info(
            f"💡 At retirement (age {p2['retirement_age']}), this withdrawal will be approximately "
            f"**${w_at_ret:,.0f}/mo** in nominal terms "
            f"(compounded from {int(r['withdrawal_base_year'])} at {r['inflation_rate']}% inflation)."
        )

    st.divider()

    # ── Income Streams ────────────────────────────────────────────────────────
    st.subheader("💰 Retirement Income Streams")
    st.caption(
        "Add all pension and income sources. Enter the **present value** in today's (or any reference year's) "
        "dollars and specify the **PV base year**. If *Inflation Adjusted* is on, the payout is compounded "
        "from that base year to the actual payment date at the inflation rate above. Leave it off for a "
        "fixed / nominal amount."
    )

    income_streams = data.setdefault("income_streams", [])

    # ── Add new stream ────────────────────────────────────────────────────────
    with st.expander("➕ Add Income Stream", expanded=len(income_streams) == 0):
        na1, na2, na3, na4, na5, na6 = st.columns([3, 2, 2, 2, 2, 1])
        new_label  = na1.text_input("Name",       value="Social Security", key="ns_label")
        new_amount = na2.number_input("Monthly ($)", 0.0, value=1000.0, step=50.0, key="ns_amount",
                                      help="Present value as of the base year below")
        new_base   = na3.number_input("PV Base Year", 2000, 2080, datetime.now().year, 1, key="ns_base",
                                      help="Year your amount is expressed in")
        new_age    = na4.number_input("Start Age", 50, 100, 67, 1, key="ns_age")
        new_inf    = na5.checkbox("Inflation Adj.", value=True, key="ns_inf",
                                  help="Compound from PV base year to payment date at the inflation rate")
        if na6.button("➕ Add"):
            income_streams.append({
                "label": new_label,
                "monthly_amount": float(new_amount),
                "base_year": int(new_base),
                "start_age": int(new_age),
                "adjust_for_inflation": bool(new_inf),
            })
            save_data(data, note=f"Added income stream: {new_label} ${new_amount:,.0f}/mo (PV {new_base}) from age {new_age}")
            st.rerun()

    # ── Existing streams ──────────────────────────────────────────────────────
    if income_streams:
        inf_rate  = float(r["inflation_rate"])
        now_dt3   = datetime.now()
        age_now3  = (now_dt3.year - int(data["profile"]["birth_year"])) + \
                    (now_dt3.month - int(data["profile"]["birth_month"])) / 12

        # Column headers
        hcols = st.columns([3, 2, 2, 2, 2, 2, 1])
        for hc, hl in zip(hcols, ["Name", "Monthly (PV $)", "PV Base Year", "Start Age", "Est. at Start", "Inflation Adj.", ""]):
            hc.markdown(f"**{hl}**")
        st.markdown("---")

        for i, s in enumerate(income_streams):
            yrs_to_start = max(0.0, float(s["start_age"]) - age_now3)
            base_yr      = int(s.get("base_year", now_dt3.year))
            base_amt     = float(s["monthly_amount"])
            if s.get("adjust_for_inflation", True) and inf_rate > 0:
                # months from base_year/Jan to start age month
                months_base_to_start = months_between(base_yr, 1, now_dt3.year, now_dt3.month) + int(yrs_to_start * 12)
                amt_at_start = base_amt * ((1 + inf_rate / 100 / 12) ** months_base_to_start)
                at_start_str = f"~${amt_at_start:,.0f}/mo"
            else:
                at_start_str = f"${base_amt:,.0f}/mo (fixed)"

            ec = st.columns([3, 2, 2, 2, 2, 2, 1])
            s["label"]          = ec[0].text_input("Name",       value=s["label"],              key=f"sl_{i}", label_visibility="collapsed")
            s["monthly_amount"] = ec[1].number_input("Amt",      min_value=0.0, value=base_amt,  step=50.0, format="%.2f", key=f"sa_{i}", label_visibility="collapsed")
            s["base_year"]      = ec[2].number_input("Base Yr",  min_value=2000, max_value=2080, value=base_yr, step=1, key=f"sb_{i}", label_visibility="collapsed")
            s["start_age"]      = ec[3].number_input("Start",    min_value=50,  max_value=100,  value=int(s["start_age"]), step=1, key=f"ss_{i}", label_visibility="collapsed")
            ec[4].markdown(at_start_str)
            s["adjust_for_inflation"] = ec[5].checkbox("Adj.", value=bool(s.get("adjust_for_inflation", True)), key=f"si_{i}", label_visibility="collapsed")
            if ec[6].button("🗑️", key=f"del_is_{i}"):
                removed = income_streams.pop(i)
                save_data(data, note=f"Removed income stream: {removed['label']}")
                st.rerun()

        total_pv = sum(float(s["monthly_amount"]) for s in income_streams)
        st.info(f"📊 **{len(income_streams)}** income stream(s) · combined PV: **${total_pv:,.0f}/mo**")
    else:
        st.info("No income streams yet. Use the form above to add Social Security, pension, annuity, rental income, etc.")

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
            save_data(data, note=f"Added snapshot: ${sn_bal:,.0f} on {sn_year}-{sn_month:02d}" + (f" ({sn_label})" if sn_label else ""))
            st.rerun()

    if snapshots:
        _df_snap = run_projection(data)
        st.subheader("Recorded Snapshots")
        for i, sn in enumerate(snapshots):
            yr, mo, actual = int(sn["year"]), int(sn["month"]), float(sn["balance"])
            label = sn.get("label", "")
            match = _df_snap[(_df_snap["year"] == yr) & (_df_snap["month"] == mo)]
            if not match.empty:
                projected = match.iloc[0]["balance"]
                diff = actual - projected
                pct  = (diff / projected * 100) if projected > 0 else 0.0
                status = (f"✅ Ahead by **${diff:,.0f}** ({pct:+.1f}%)" if diff >= 0
                          else f"⚠️ Behind by **${abs(diff):,.0f}** ({pct:+.1f}%)")
                color = "success" if diff >= 0 else "warning"
            else:
                projected, status, color = None, "ℹ️ Outside projection range", "info"

            cols = st.columns([2, 2, 2, 3, 1])
            cols[0].write(f"📅 **{yr}-{mo:02d}**" + (f"  _{label}_" if label else ""))
            cols[1].write(f"Actual: **${actual:,.0f}**")
            cols[2].write(f"Projected: **${projected:,.0f}**" if projected else "—")
            getattr(cols[3], color)(status)
            if cols[4].button("🗑️", key=f"del_sn_{i}"):
                snapshots.pop(i)
                save_data(data, note=f"Removed snapshot {yr}-{mo:02d}")
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
        st.caption(f"**{len(changelog)} version{'s' if len(changelog) != 1 else ''}** · latest is **v{latest_v}**")

        for entry in reversed(changelog):
            v         = entry["version"]
            ts        = entry["timestamp"].replace("T", " ")
            note      = entry["note"]
            is_latest = (v == latest_v)

            with st.expander(
                f"{'🟢 ' if is_latest else ''}**v{v}** — {ts}   _{note[:80]}{'…' if len(note) > 80 else ''}_",
                expanded=is_latest,
            ):
                st.markdown(f"**Note:** {note}")
                s    = entry["settings"]
                prof = s.get("profile", {})
                cont = s.get("contributions", {})
                grow = s.get("growth", {})
                ret  = s.get("retirement", {})
                iss  = s.get("income_streams", [])

                c1, c2, c3 = st.columns(3)
                c1.markdown(
                    f"**Profile**  \nSavings: ${float(prof.get('current_savings', 0)):,.0f}  \n"
                    f"Retire at: {prof.get('retirement_age', '—')}  \nEnd age: {prof.get('end_age', '—')}"
                )
                c2.markdown(
                    f"**Contributions**  \nMonthly: ${float(cont.get('monthly_contribution', 0)):,.0f}  \n"
                    f"Yearly: ${float(cont.get('yearly_contribution', 0)):,.0f}  \n"
                    f"Growth: {cont.get('contribution_growth_rate', 0)}%"
                )
                c3.markdown(
                    f"**Growth & Withdrawal**  \nReturn: {grow.get('annual_interest_rate', 0)}%  \n"
                    f"Withdrawal: ${float(ret.get('monthly_withdrawal', 0)):,.0f}/mo (PV {ret.get('withdrawal_base_year', '—')})  \n"
                    f"Inflation: {ret.get('inflation_rate', 0)}%"
                )
                if iss:
                    lines = "  \n".join(
                        f"• {s2['label']}: ${float(s2['monthly_amount']):,.0f}/mo "
                        f"(PV {s2.get('base_year','?')}) · age {s2['start_age']} · "
                        f"{'infl. adj.' if s2.get('adjust_for_inflation') else 'fixed'}"
                        for s2 in iss
                    )
                    st.markdown(f"**Income Streams ({len(iss)})**  \n{lines}")

                prev_entries = [e for e in changelog if e["version"] == v - 1]
                if prev_entries:
                    diff_text = _diff_summary(prev_entries[0]["settings"], s)
                    if diff_text and diff_text != "No field changes detected":
                        st.markdown(f"**Changes from v{v-1}:** `{diff_text}`")

                if not is_latest:
                    if st.button(f"↩️ Restore v{v}", key=f"restore_{v}"):
                        restore_version(data, v)

        st.divider()
        with st.expander("📤 Export Change Log as CSV"):
            rows = []
            for e in changelog:
                s = e["settings"]
                rows.append({
                    "version":            e["version"],
                    "timestamp":          e["timestamp"],
                    "note":               e["note"],
                    "savings":            s.get("profile", {}).get("current_savings", ""),
                    "retire_age":         s.get("profile", {}).get("retirement_age", ""),
                    "monthly_contrib":    s.get("contributions", {}).get("monthly_contribution", ""),
                    "return_rate":        s.get("growth", {}).get("annual_interest_rate", ""),
                    "monthly_withdrawal": s.get("retirement", {}).get("monthly_withdrawal", ""),
                    "withdrawal_base_yr": s.get("retirement", {}).get("withdrawal_base_year", ""),
                    "inflation":          s.get("retirement", {}).get("inflation_rate", ""),
                    "income_streams":     len(s.get("income_streams", [])),
                })
            st.download_button("⬇️ Download CSV", data=pd.DataFrame(rows).to_csv(index=False),
                               file_name="retirement_changelog.csv", mime="text/csv")


# ── Tab: Dashboard ────────────────────────────────────────────────────────────
with tabs[0]:
    st.header("Dashboard")
    df = run_projection(data)

    if df.empty:
        st.warning("Please complete your profile settings first.")
    else:
        p_d  = data["profile"]
        now  = datetime.now()
        age_now_d    = (now.year - int(p_d["birth_year"])) + (now.month - int(p_d["birth_month"])) / 12
        savings_yr_d = int(p_d.get("savings_year",  now.year))
        savings_mo_d = int(p_d.get("savings_month", now.month))
        age_at_sav_d = (savings_yr_d - int(p_d["birth_year"])) + (savings_mo_d - int(p_d["birth_month"])) / 12
        yrs_to_ret_d = max(0.0, int(p_d["retirement_age"]) - age_at_sav_d)
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

        # Snapshot tracking banner
        snapshots = data.get("snapshots", [])
        if snapshots:
            latest_snap = None
            for sn in reversed(snapshots):
                row = df[(df["year"] == int(sn["year"])) & (df["month"] == int(sn["month"]))]
                if not row.empty:
                    latest_snap = (sn, row.iloc[0]["balance"])
                    break
            if latest_snap:
                sn, proj = latest_snap
                actual   = float(sn["balance"])
                diff     = actual - proj
                pct      = (diff / proj * 100) if proj > 0 else 0.0
                date_str = f"{int(sn['year'])}-{int(sn['month']):02d}"
                label    = sn.get("label", "")
                msg = (
                    f"{'✅ Ahead' if diff >= 0 else '⚠️ Behind'} as of latest snapshot "
                    f"({date_str}{' · ' + label if label else ''}): "
                    f"actual **${actual:,.0f}** vs projected **${proj:,.0f}** "
                    f"({'▲' if diff >= 0 else '▼'} **${abs(diff):,.0f}** / {pct:+.1f}%)"
                )
                (st.success if diff >= 0 else st.warning)(msg)

        # ── Main balance chart ──
        st.subheader("Portfolio Balance Over Time")
        df_yr = df.groupby("age_int").last().reset_index()
        pre   = df_yr[~df_yr["is_retired"]]
        post  = df_yr[df_yr["is_retired"]]

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
                    marker=dict(symbol="star", size=14, color="#9c27b0"),
                    name=ls.get("label", "Lump Sum"),
                    hovertemplate=f"{ls.get('label','Lump Sum')}<br>${float(ls['amount']):,.0f}<extra></extra>"))

        if snapshots:
            snap_ages, snap_bals, snap_texts = [], [], []
            for sn in snapshots:
                row = df[(df["year"] == int(sn["year"])) & (df["month"] == int(sn["month"]))]
                if not row.empty:
                    age_val    = row.iloc[0]["age"]
                    proj_val   = row.iloc[0]["balance"]
                    actual_val = float(sn["balance"])
                    diff       = actual_val - proj_val
                    pct        = (diff / proj_val * 100) if proj_val > 0 else 0.0
                    snap_ages.append(age_val)
                    snap_bals.append(actual_val)
                    lbl = sn.get("label", "")
                    snap_texts.append(
                        f"{lbl + '<br>' if lbl else ''}Actual: ${actual_val:,.0f}<br>"
                        f"Projected: ${proj_val:,.0f}<br>{'▲' if diff >= 0 else '▼'} {pct:+.1f}%"
                    )
            if snap_ages:
                fig.add_trace(go.Scatter(x=snap_ages, y=snap_bals, mode="lines+markers",
                    name="Actual (Snapshots)", line=dict(color="#e53935", width=2.5),
                    marker=dict(size=11,
                        color=["#43a047" if float(s["balance"]) >= df[(df["year"] == int(s["year"])) & (df["month"] == int(s["month"]))]["balance"].iloc[0]
                               else "#e53935" for s in snapshots
                               if not df[(df["year"] == int(s["year"])) & (df["month"] == int(s["month"]))].empty],
                        symbol="circle", line=dict(color="white", width=1.5)),
                    text=snap_texts, hovertemplate="%{text}<extra></extra>"))

        fig.add_vline(x=int(p_d["retirement_age"]), line_dash="dot", line_color="gray",
                      annotation_text="Retirement", annotation_position="top right")
        fig.update_layout(xaxis_title="Age", yaxis_title=f"Balance ({curr})",
                          hovermode="x unified", height=420, margin=dict(t=40, b=40),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left"))
        st.plotly_chart(fig, use_container_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Annual Interest Earned")
            di   = df.groupby("age_int")["interest"].sum().reset_index()
            fig2 = go.Figure(go.Bar(x=di["age_int"], y=di["interest"], marker_color="#43a047"))
            fig2.update_layout(xaxis_title="Age", yaxis_title=curr, height=300, margin=dict(t=20))
            st.plotly_chart(fig2, use_container_width=True)
        with col_b:
            st.subheader("Annual Contributions")
            dc   = df.groupby("age_int")["contribution"].sum().reset_index()
            fig3 = go.Figure(go.Bar(x=dc["age_int"], y=dc["contribution"], marker_color="#1a73e8"))
            fig3.update_layout(xaxis_title="Age", yaxis_title=curr, height=300, margin=dict(t=20))
            st.plotly_chart(fig3, use_container_width=True)

        if df["is_retired"].any():
            st.subheader("Retirement Cash Flow (Annual)")
            df_ret = df[df["is_retired"]].groupby("age_int").agg(
                withdrawal=("withdrawal", "sum"),
                income=("income", "sum"),
                net_withdrawal=("net_withdrawal", "sum"),
            ).reset_index()
            income_label = f"Pension / Income Streams ({len(data.get('income_streams', []))})"
            fig4 = go.Figure()
            fig4.add_trace(go.Bar(x=df_ret["age_int"], y=df_ret["withdrawal"], name="Gross Withdrawal",  marker_color="#ef5350"))
            fig4.add_trace(go.Bar(x=df_ret["age_int"], y=df_ret["income"],     name=income_label,        marker_color="#43a047"))
            fig4.add_trace(go.Scatter(x=df_ret["age_int"], y=df_ret["net_withdrawal"], name="Net from Savings",
                                      line=dict(color="#f9a825", width=2, dash="dot")))
            fig4.update_layout(barmode="overlay", xaxis_title="Age", yaxis_title=curr,
                               height=320, legend=dict(orientation="h"), margin=dict(t=20))
            st.plotly_chart(fig4, use_container_width=True)

        # ── Year-by-Year Table: monthly avg + annual totals ───────────────────
        with st.expander("📋 Year-by-Year Table"):
            # Annual aggregates
            agg = df.groupby("age_int").agg(
                year=("year", "last"),
                balance=("balance", "last"),          # end-of-year balance
                interest_annual=("interest", "sum"),
                contrib_annual=("contribution", "sum"),
                lump_annual=("lump_sum", "sum"),
                withdrawal_annual=("withdrawal", "sum"),
                income_annual=("income", "sum"),
                net_wd_annual=("net_withdrawal", "sum"),
                month_count=("month", "count"),       # to compute monthly avg
            ).reset_index()

            # Monthly averages (divide annual totals by number of months in that year)
            for col in ["interest", "contrib", "lump", "withdrawal", "income", "net_wd"]:
                agg[f"{col}_monthly_avg"] = agg[f"{col}_annual"] / agg["month_count"]

            tbl_rows = []
            for _, row in agg.iterrows():
                tbl_rows.append({
                    "Age":                    int(row["age_int"]),
                    "Year":                   int(row["year"]),
                    "End Balance":            f"${row['balance']:,.0f}",
                    # ── Annual totals ──
                    "Interest (Annual)":      f"${row['interest_annual']:,.0f}",
                    "Contributions (Annual)": f"${row['contrib_annual']:,.0f}",
                    "Lump Sums (Annual)":     f"${row['lump_annual']:,.0f}",
                    "Withdrawal (Annual)":    f"${row['withdrawal_annual']:,.0f}",
                    "Income (Annual)":        f"${row['income_annual']:,.0f}",
                    "Net W/D (Annual)":       f"${row['net_wd_annual']:,.0f}",
                    # ── Monthly averages ──
                    "Interest (Monthly Avg)":      f"${row['interest_monthly_avg']:,.0f}",
                    "Contributions (Monthly Avg)": f"${row['contrib_monthly_avg']:,.0f}",
                    "Withdrawal (Monthly Avg)":    f"${row['withdrawal_monthly_avg']:,.0f}",
                    "Income (Monthly Avg)":        f"${row['income_monthly_avg']:,.0f}",
                    "Net W/D (Monthly Avg)":       f"${row['net_wd_monthly_avg']:,.0f}",
                })

            tbl_df = pd.DataFrame(tbl_rows)

            view_mode = st.radio(
                "Show columns",
                ["Annual Totals", "Monthly Averages", "Both"],
                horizontal=True,
                key="tbl_view",
            )
            annual_cols  = ["Age", "Year", "End Balance",
                            "Interest (Annual)", "Contributions (Annual)", "Lump Sums (Annual)",
                            "Withdrawal (Annual)", "Income (Annual)", "Net W/D (Annual)"]
            monthly_cols = ["Age", "Year", "End Balance",
                            "Interest (Monthly Avg)", "Contributions (Monthly Avg)",
                            "Withdrawal (Monthly Avg)", "Income (Monthly Avg)", "Net W/D (Monthly Avg)"]

            if view_mode == "Annual Totals":
                show_cols = annual_cols
            elif view_mode == "Monthly Averages":
                show_cols = monthly_cols
            else:
                show_cols = list(dict.fromkeys(annual_cols + monthly_cols))  # deduplicated, ordered

            st.dataframe(tbl_df[show_cols], use_container_width=True, hide_index=True)

        with st.expander("🔬 Sensitivity: Balance at Retirement vs. Return Rate"):
            rates, balances = [3, 4, 5, 6, 7, 8, 9, 10], []
            orig = data["growth"]["annual_interest_rate"]
            for rate in rates:
                data["growth"]["annual_interest_rate"] = rate
                tmp = run_projection(data)
                bal = tmp[tmp["is_retired"]]["balance"].iloc[0] if tmp["is_retired"].any() else tmp["balance"].iloc[-1]
                balances.append(bal)
            data["growth"]["annual_interest_rate"] = orig
            fig5 = go.Figure(go.Scatter(x=rates, y=balances, mode="lines+markers",
                line=dict(color="#1a73e8", width=2.5), marker=dict(size=8)))
            fig5.update_layout(xaxis_title="Annual Return Rate (%)",
                               yaxis_title=f"Balance at Retirement ({curr})",
                               height=300, margin=dict(t=20))
            st.plotly_chart(fig5, use_container_width=True)
