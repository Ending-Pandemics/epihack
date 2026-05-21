#!/usr/bin/env python3
"""
HealthMind API - One Health 3-Layer REST API
============================================
- Auto-creates epihack.db with 4 tables on first run
- Seeds 100 human cases from old DB on first run
- Supports all 7 domain combinations

Local:   uvicorn api:app --reload
Deploy:  uvicorn api:app --host 0.0.0.0 --port 8000

Endpoints:
  GET  /              - welcome
  GET  /health        - status check
  GET  /cases         - list all cases
  GET  /cases/{id}    - get one case
  POST /report        - submit new incident (any domain combo)
  POST /analyze       - analyze a case (3 layers)

Requirements:
  pip install fastapi uvicorn openai requests openpyxl

Environment:
  export LLAMA_API_KEY="your-key"
  export DATABASE_URL="epihack.db"   # default
"""

import os, sqlite3, datetime, requests, uuid
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from openai import OpenAI

# ── CONFIG ───────────────────────────────────────────────────────────
DB_PATH  = os.environ.get("DATABASE_URL", "epihack.db")
BASE_URL = "https://llm-api.cyverse.ai/v1"
MODEL    = "llama-3.3-70b-instruct-quantized"
API_KEY  = os.environ.get("LLAMA_API_KEY")
WINDOW   = 7

AZDHS_URL = "https://www.azdhs.gov/preparedness/epidemiology-disease-control/index.php"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

SYMPTOM_COLS = [
    "cough_congestion", "nauseas_vomiting", "difficulty_breathing",
    "sore_throat", "rash", "fever", "chills", "diarrhea",
    "bleeding_from_body_openings", "red_eyes",
    "muscle_or_body_aches_and_pains", "discolored_or_bloody_urine",
    "loss_of_smell_or_taste", "yellow_skin_yellow_eyes",
]
SEVERE_MARKERS = [
    "difficulty_breathing", "bleeding_from_body_openings",
    "discolored_or_bloody_urine", "yellow_skin_yellow_eyes",
]
AZ_REFERENCE = """
Arizona One Health reference (advisory grounding):
- Fever + chills + muscle aches: vector-borne illness (West Nile, dengue,
  Rocky Mountain spotted fever); Arizona mosquito/tick season May-October.
- Difficulty breathing: critical severity marker.
- Bleeding from body openings: urgent, possible viral hemorrhagic condition.
- Yellow skin/eyes: hepatitis or liver involvement.
- Loss of smell/taste + cough + fever: respiratory viral infection.
- Diarrhea + nausea/vomiting: enteric/foodborne or waterborne illness.
- Unusual vector presence + high density: raises human risk.
- Flooding + water contamination: raises enteric and mosquito-borne risk.
- Concurrent human + animal + environmental signals in same area:
  core One Health concern — possible zoonotic spillover.
- Report to AZDHS when a reportable condition is suspected.
"""

# ── DATABASE ─────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    """Create all 4 tables if they don't exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id        TEXT PRIMARY KEY,
                date_of_report TEXT,
                domain_human   TEXT DEFAULT 'NO',
                domain_animal  TEXT DEFAULT 'NO',
                domain_env     TEXT DEFAULT 'NO',
                created_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS human_data (
                case_id                      TEXT PRIMARY KEY,
                age                          INTEGER,
                sex                          TEXT,
                email                        TEXT,
                occupation                   TEXT,
                postal_code                  TEXT,
                phone_number                 TEXT,
                household_member_id          TEXT,
                geographical_coordinates     TEXT,
                date_of_illness              TEXT,
                no_symptoms                  TEXT,
                symptoms                     TEXT,
                cough_congestion             TEXT,
                nauseas_vomiting             TEXT,
                difficulty_breathing         TEXT,
                sore_throat                  TEXT,
                rash                         TEXT,
                fever                        TEXT,
                chills                       TEXT,
                diarrhea                     TEXT,
                bleeding_from_body_openings  TEXT,
                red_eyes                     TEXT,
                muscle_or_body_aches_and_pains TEXT,
                discolored_or_bloody_urine   TEXT,
                loss_of_smell_or_taste       TEXT,
                yellow_skin_yellow_eyes      TEXT,
                absent_from_work             TEXT,
                absent_from_school           TEXT,
                sought_healthcare            TEXT,
                attend_mass_gathering        TEXT,
                tick_or_insect_bite          TEXT,
                animal_bite                  TEXT,
                history_of_travel            TEXT,
                contact_live_animals         TEXT,
                contact_dead_sick_animals    TEXT,
                contact_sick_individual      TEXT,
                digital_biomarker_signal     TEXT,
                photo                        TEXT,
                diagnostic_lab_confirmation  TEXT,
                other_notes                  TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS animal_data (
                case_id                  TEXT PRIMARY KEY,
                wildlife_date            TEXT,
                wildlife_location        TEXT,
                wildlife_species         TEXT,
                wildlife_dead_count      INTEGER,
                livestock_date           TEXT,
                livestock_location       TEXT,
                livestock_species        TEXT,
                livestock_sick_count     INTEGER,
                livestock_dead_count     INTEGER,
                other_notes              TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS environment_data (
                case_id                     TEXT PRIMARY KEY,
                date_of_incident            TEXT,
                location_of_vector_spotting TEXT,
                unusual_presence_of_vectors TEXT,
                density_of_vectors          TEXT,
                flooding                    TEXT,
                water_contamination         TEXT,
                other_notes                 TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            )
        """)
    print("✅ Database tables ready.")

def seed_human_cases():
    """Seed 100 human cases from old epihack.db if cases table is empty."""
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        if count > 0:
            print(f"✅ DB already has {count} cases. Skipping seed.")
            return

    # Try to read from old single-table DB
    old_db = "epihack_old.db"
    if not os.path.exists(old_db):
        print("⚠️  No old DB found to seed from. Starting fresh.")
        return

    old_conn = sqlite3.connect(old_db)
    old_conn.row_factory = sqlite3.Row
    rows = old_conn.execute("SELECT * FROM incidents").fetchall()
    old_conn.close()

    with get_db() as conn:
        for i, r in enumerate(rows, start=1):
            cid = f"HM-{i:03d}"
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO cases VALUES (?,?,?,?,?,?)",
                (cid, r["date_of_report"], "YES", "NO", "NO", now)
            )
            conn.execute("""
                INSERT OR IGNORE INTO human_data
                (case_id, age, sex, email, occupation, postal_code,
                 phone_number, household_member_id, date_of_illness,
                 no_symptoms, symptoms, cough_congestion, nauseas_vomiting,
                 difficulty_breathing, sore_throat, rash, fever, chills,
                 diarrhea, bleeding_from_body_openings, red_eyes,
                 muscle_or_body_aches_and_pains, discolored_or_bloody_urine,
                 loss_of_smell_or_taste, yellow_skin_yellow_eyes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                cid, r["age"], r["sex"], r["email"], r["occupation"],
                r["postal_code"], r["phone_number"], r["household_member_id"],
                r["date_of_illness"], r["no_symptoms"], r["symptoms"],
                r["cough_congestion"], r["nauseas_vomiting"],
                r["difficulty_breathing"], r["sore_throat"], r["rash"],
                r["fever"], r["chills"], r["diarrhea"],
                r["bleeding_from_body_openings"], r["red_eyes"],
                r["muscle_or_body_aches_and_pains"],
                r["discolored_or_bloody_urine"],
                r["loss_of_smell_or_taste"], r["yellow_skin_yellow_eyes"],
            ))
    print(f"✅ Seeded {len(rows)} human cases.")

def next_case_id():
    """Generate next case ID: HM-101, HM-102 etc."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT case_id FROM cases ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "HM-001"
        last = row["case_id"]
        try:
            num = int(last.split("-")[1]) + 1
            return f"HM-{num:03d}"
        except Exception:
            return f"HM-{str(uuid.uuid4())[:6].upper()}"

# ── APP ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="HealthMind API",
    description="One Health 3-Layer Query System — Human / Animal / Environment",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

llm_client = None

@app.on_event("startup")
def startup():
    global llm_client
    init_db()
    seed_human_cases()
    llm_client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    print("✅ HealthMind API started.")

# ── REQUEST MODELS ────────────────────────────────────────────────────
class HumanInput(BaseModel):
    age:                          Optional[int]  = None
    sex:                          Optional[str]  = None
    email:                        Optional[str]  = None
    occupation:                   Optional[str]  = None
    postal_code:                  Optional[str]  = None
    phone_number:                 Optional[str]  = None
    household_member_id:          Optional[str]  = None
    geographical_coordinates:     Optional[str]  = None
    date_of_illness:              Optional[str]  = None
    no_symptoms:                  Optional[str]  = None
    symptoms:                     Optional[str]  = None
    cough_congestion:             Optional[str]  = None
    nauseas_vomiting:             Optional[str]  = None
    difficulty_breathing:         Optional[str]  = None
    sore_throat:                  Optional[str]  = None
    rash:                         Optional[str]  = None
    fever:                        Optional[str]  = None
    chills:                       Optional[str]  = None
    diarrhea:                     Optional[str]  = None
    bleeding_from_body_openings:  Optional[str]  = None
    red_eyes:                     Optional[str]  = None
    muscle_or_body_aches_and_pains: Optional[str] = None
    discolored_or_bloody_urine:   Optional[str]  = None
    loss_of_smell_or_taste:       Optional[str]  = None
    yellow_skin_yellow_eyes:      Optional[str]  = None
    absent_from_work:             Optional[str]  = None
    absent_from_school:           Optional[str]  = None
    sought_healthcare:            Optional[str]  = None
    attend_mass_gathering:        Optional[str]  = None
    tick_or_insect_bite:          Optional[str]  = None
    animal_bite:                  Optional[str]  = None
    history_of_travel:            Optional[str]  = None
    contact_live_animals:         Optional[str]  = None
    contact_dead_sick_animals:    Optional[str]  = None
    contact_sick_individual:      Optional[str]  = None
    digital_biomarker_signal:     Optional[str]  = None
    photo:                        Optional[str]  = None
    diagnostic_lab_confirmation:  Optional[str]  = None
    other_notes:                  Optional[str]  = None

class AnimalInput(BaseModel):
    wildlife_date:        Optional[str] = None
    wildlife_location:    Optional[str] = None
    wildlife_species:     Optional[str] = None
    wildlife_dead_count:  Optional[int] = None
    livestock_date:       Optional[str] = None
    livestock_location:   Optional[str] = None
    livestock_species:    Optional[str] = None
    livestock_sick_count: Optional[int] = None
    livestock_dead_count: Optional[int] = None
    other_notes:          Optional[str] = None

class EnvironmentInput(BaseModel):
    date_of_incident:            Optional[str] = None
    location_of_vector_spotting: Optional[str] = None
    unusual_presence_of_vectors: Optional[str] = None
    density_of_vectors:          Optional[str] = None
    flooding:                    Optional[str] = None
    water_contamination:         Optional[str] = None
    other_notes:                 Optional[str] = None

class ReportRequest(BaseModel):
    domain_human:       bool = False
    domain_animal:      bool = False
    domain_environment: bool = False
    human:              Optional[HumanInput]      = None
    animal:             Optional[AnimalInput]     = None
    environment:        Optional[EnvironmentInput]= None

class AnalyzeRequest(BaseModel):
    case_id: str

# ── LLM ──────────────────────────────────────────────────────────────
def ask_llm(system, user):
    try:
        r = llm_client.chat.completions.create(
            model=MODEL,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            temperature=0.3, max_tokens=500,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"[LLM error: {e}]"


# ── NOTE CLASSIFIER ──────────────────────────────────────────────────
def classify_note(note, domain="human"):
    """Returns (is_relevant: bool, note_text: str)"""
    if not note or not note.strip():
        return False, None
    try:
        result = ask_llm(
            f"You are a One Health triage classifier for the {domain} domain. "
            "Determine if the following note is relevant to human health, "
            "animal health, or environmental health. "
            "Consider symptoms, exposures, observations, locations, "
            "animal incidents, environmental conditions, or anything "
            "that could affect health outcomes. "
            "Reply with ONLY one word: RELEVANT or IRRELEVANT.",
            f'Note: "{note.strip()}"'
        )
        relevant = "RELEVANT" in result.upper()
        if relevant:
            return True, f'Clinically relevant note: "{note.strip()}"'
        else:
            return False, f'Non-clinical note provided: "{note.strip()}" (not relevant to this incident).'
    except Exception:
        return False, None

# ── LAYER 1 ───────────────────────────────────────────────────────────
def run_layer1_human(h):
    if not h:
        return None
    sx     = [c for c in SYMPTOM_COLS if str(h.get(c,"")).upper() == "YES"]
    severe = [c for c in sx if c in SEVERE_MARKERS]
    sev    = "HIGH" if severe else ("MODERATE" if sx else "LOW")
    facts  = (f"Human case: {h.get('age')}yo {h.get('sex')}, "
              f"{h.get('occupation')}. Illness: {h.get('date_of_illness')}. "
              f"Symptoms: {', '.join(sx) or 'none'}. "
              f"Severe markers: {', '.join(severe) or 'none'}.")
    relevant, note_text = classify_note(h.get("other_notes"), domain="human")
    if note_text:
        facts += f" {note_text}"
    narrative = ask_llm(
        "You are a One Health incident interpreter. Restate in 2-3 plain "
        "sentences. If a clinically relevant note is included, incorporate it. "
        "If a non-clinical note is mentioned, briefly acknowledge it was disregarded. "
        "Do not diagnose. End with severity level.",
        facts + f"\nSeverity: {sev}"
    )
    return {"symptoms": sx, "severe_markers": severe,
            "severity": sev, "narrative": narrative}

def run_layer1_animal(a):
    if not a:
        return None
    facts = (
        f"Animal incident: Wildlife — {a.get('wildlife_species')} at "
        f"{a.get('wildlife_location')}, {a.get('wildlife_dead_count')} dead. "
        f"Livestock — {a.get('livestock_species')} at "
        f"{a.get('livestock_location')}, {a.get('livestock_sick_count')} sick, "
        f"{a.get('livestock_dead_count')} dead."
    )
    dead = (a.get("wildlife_dead_count") or 0) + (a.get("livestock_dead_count") or 0)
    sick = a.get("livestock_sick_count") or 0
    sev  = "HIGH" if dead >= 8 else ("MODERATE" if (dead + sick) > 3 else "LOW")
    relevant, note_text = classify_note(a.get("other_notes"), domain="animal")
    if note_text:
        facts += f" {note_text}"
    narrative = ask_llm(
        "You are a One Health incident interpreter. Restate this animal "
        "incident in 2-3 plain sentences. If a relevant note is included, "
        "incorporate it. If non-clinical, briefly acknowledge and disregard. "
        "End with severity level.",
        facts + f"\nSeverity: {sev}"
    )
    return {"severity": sev, "narrative": narrative}

def run_layer1_env(e):
    if not e:
        return None
    facts = (
        f"Environmental incident at {e.get('location_of_vector_spotting')} "
        f"on {e.get('date_of_incident')}. "
        f"Unusual vectors: {e.get('unusual_presence_of_vectors')}, "
        f"density: {e.get('density_of_vectors')}. "
        f"Flooding: {e.get('flooding')}, "
        f"water contamination: {e.get('water_contamination')}."
    )
    density = str(e.get("density_of_vectors","")).upper()
    unusual = str(e.get("unusual_presence_of_vectors","")).upper()
    sev = ("HIGH"     if density == "HIGH" and unusual == "YES" else
           "MODERATE" if unusual == "YES" else "LOW")
    relevant, note_text = classify_note(e.get("other_notes"), domain="environment")
    if note_text:
        facts += f" {note_text}"
    narrative = ask_llm(
        "You are a One Health incident interpreter. Restate this environmental "
        "incident in 2-3 plain sentences. If a relevant note is included, "
        "incorporate it. If non-clinical, briefly acknowledge and disregard. "
        "End with severity level.",
        facts + f"\nSeverity: {sev}"
    )
    return {"severity": sev, "narrative": narrative}

# ── LAYER 2 ───────────────────────────────────────────────────────────
def run_layer2(domains, l1_human, l1_animal, l1_env, h, a, e):
    parts = []
    if l1_human:
        parts.append(f"Human: symptoms {l1_human['symptoms']}, "
                     f"severity {l1_human['severity']}.")
    if l1_animal:
        parts.append(f"Animal: {l1_animal['narrative']}")
    if l1_env:
        parts.append(f"Environment: {l1_env['narrative']}")

    one_health = len(domains) > 1
    advisory = ask_llm(
        "You are a One Health risk advisor. Using ONLY the reference context, "
        "give a 3-4 sentence contextual risk advisory covering all reported "
        "domains. Highlight any One Health connections if multiple domains "
        "are reported. State this is decision-support, not medical advice.",
        f"Reference:\n{AZ_REFERENCE}\n\n"
        f"Domains reported: {', '.join(domains)}.\n"
        f"Incident summary:\n" + "\n".join(parts)
    )
    link = None
    if one_health:
        link = ask_llm(
            "In one sentence, describe the One Health connection between "
            "the reported domains.",
            f"Domains: {', '.join(domains)}.\n" + "\n".join(parts)
        )
    return {
        "advisory": advisory,
        "domains_considered": domains,
        "one_health_link": link,
    }

# ── LAYER 3 ───────────────────────────────────────────────────────────
# RSS feeds for live health alerts (open, no auth required)
RSS_FEEDS = [
    ("WHO Disease Outbreaks", "https://www.who.int/feeds/entity/csr/don/en/rss.xml"),
    ("CDC Health Alerts",     "https://emergency.cdc.gov/han/feed.asp"),
    ("USDA APHIS Animal Health", "https://www.aphis.usda.gov/rss/aphis_news.xml"),
    ("ReliefWeb Health",      "https://reliefweb.int/updates/rss.xml?primary_country=840&source=who"),
]

def fetch_live_alerts():
    """Fetch RSS feeds, return list of (source, title, date, description)."""
    from xml.etree import ElementTree as ET
    alerts = []
    for name, url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=10, headers=HTTP_HEADERS)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            items = root.findall(".//item")
            for item in items[:5]:   # top 5 per feed
                title = item.findtext("title", "").strip()
                date  = item.findtext("pubDate", "")[:25].strip()
                desc  = item.findtext("description", "").strip()[:300]
                if title:
                    alerts.append({
                        "source": name,
                        "title":  title,
                        "date":   date,
                        "description": desc,
                    })
        except Exception:
            continue
    return alerts

def summarize_live_alerts(alerts, domain_context):
    """Ask LLM to filter alerts relevant to the case domains."""
    if not alerts:
        return "No live alerts retrieved from health RSS feeds.", False
    alert_text = "\n".join(
        f"[{a['source']} | {a['date']}] {a['title']}: {a['description']}"
        for a in alerts
    )
    result = ask_llm(
        f"You are a One Health live alert analyst. "
        f"The reported incident involves: {domain_context}. "
        f"From the following live health alerts fetched today, identify any "
        f"that are relevant to this case (same disease type, region, or domain). "
        f"If relevant alerts exist, summarize them in 2-3 sentences mentioning "
        f"the source and date. If none are relevant, say so clearly.",
        f"Today\'s date: {datetime.date.today():%B %d, %Y}\n\n"
        f"Live alerts:\n{alert_text}"
    )
    relevant = any(kw in result.lower() for kw in ["alert","outbreak","case","disease","report","concern"])
    return result, relevant

def run_layer3(case_id, date_str, domains=None):
    today     = datetime.date.today()
    tdate     = None
    if date_str:
        try:
            tdate = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except Exception:
            pass

    # ── PART A: Live RSS alerts ──────────────────────────────────────
    domain_context = ", ".join(domains) if domains else "human health"
    alerts         = fetch_live_alerts()
    live_summary, has_relevant = summarize_live_alerts(alerts, domain_context)
    feeds_ok = len(alerts) > 0

    # ── PART B: Historical from DB ──────────────────────────────────
    hist_by_year = {}   # {year: {"count":n, "fever":n, "human":n, "animal":n, "env":n}}
    cluster      = {"human": 0, "animal": 0, "env": 0, "fever": 0}

    if tdate:
        with get_db() as conn:
            all_rows = conn.execute(
                "SELECT c.case_id, c.domain_human, c.domain_animal, "
                "c.domain_env, c.date_of_report, h.fever, h.date_of_illness "
                "FROM cases c LEFT JOIN human_data h ON c.case_id = h.case_id "
                "WHERE c.case_id != ?", (case_id,)
            ).fetchall()

        for row in all_rows:
            d_str = row["date_of_illness"] or row["date_of_report"]
            if not d_str:
                continue
            try:
                d = datetime.datetime.strptime(d_str[:10], "%Y-%m-%d").date()
                cal_delta = abs((
                    datetime.date(2000, d.month, d.day) -
                    datetime.date(2000, tdate.month, tdate.day)
                ).days)
                if cal_delta > WINDOW:
                    continue
                yr = d.year
                if yr not in hist_by_year:
                    hist_by_year[yr] = {"count":0,"fever":0,"human":0,"animal":0,"env":0}
                hist_by_year[yr]["count"] += 1
                if str(row["fever"]).upper() == "YES":
                    hist_by_year[yr]["fever"] += 1
                if row["domain_human"]  == "YES": hist_by_year[yr]["human"]  += 1
                if row["domain_animal"] == "YES": hist_by_year[yr]["animal"] += 1
                if row["domain_env"]    == "YES": hist_by_year[yr]["env"]    += 1
                # cluster = same year as tdate
                if yr == tdate.year:
                    if row["domain_human"]  == "YES": cluster["human"]  += 1
                    if row["domain_animal"] == "YES": cluster["animal"] += 1
                    if row["domain_env"]    == "YES": cluster["env"]    += 1
                    if str(row["fever"]).upper() == "YES": cluster["fever"] += 1
            except Exception:
                continue

    # Build historical message clearly labeled by year
    hist_lines = []
    for yr in sorted(hist_by_year.keys()):
        y = hist_by_year[yr]
        label = f"In {yr}" if yr != tdate.year else f"In {yr} (same year as case)"
        hist_lines.append(
            f"{label}, around {tdate:%b %d}: {y['count']} incidents "
            f"({y['human']} human, {y['animal']} animal, {y['env']} env); "
            f"{y['fever']} with fever."
        )
    hist_msg = ("\n".join(hist_lines)
                if hist_lines
                else f"No historical data found around {tdate:%b %d} in the database.")

    total_cluster = cluster["human"] + cluster["animal"] + cluster["env"]
    elevated = cluster["fever"] >= 4

    cluster_msg = (
        f"In {tdate.year}, within ±{WINDOW} days of {tdate:%b %d}: "
        f"{total_cluster} other incidents "
        f"({cluster['human']} human, {cluster['animal']} animal, "
        f"{cluster['env']} environmental); {cluster['fever']} with fever."
        + (" ⚠ ELEVATED: possible outbreak signal." if elevated else "")
        if tdate else "No date available for cluster analysis."
    )

    summary = ask_llm(
        "You are a One Health live-data analyst. In 3-4 sentences: "
        "1) Summarize any relevant live health alerts (mention source and date). "
        "2) Describe the cluster signal from the database for the incident date, "
        "mentioning the specific year. "
        "3) Compare across years if multiple years of data exist. "
        "Always mention specific years, never say vague terms like 'last year'.",
        f"Today: {today:%B %d, %Y}\n"
        f"Case date: {tdate:%B %d, %Y if tdate else 'unknown'}\n"
        f"Live alerts summary: {live_summary}\n"
        f"Database cluster ({tdate.year if tdate else 'N/A'}): {cluster_msg}\n"
        f"Historical by year:\n{hist_msg}"
    )

    return {
        "feeds_ok":      feeds_ok,
        "feeds_checked": len(RSS_FEEDS),
        "alerts_found":  len(alerts),
        "has_relevant_alert": has_relevant,
        "current_signal": {
            "year":                tdate.year if tdate else None,
            "date_window":         f"{tdate:%b %d, %Y} ±{WINDOW} days" if tdate else None,
            "human_cases_nearby":  cluster["human"],
            "animal_incidents_nearby": cluster["animal"],
            "env_incidents_nearby": cluster["env"],
            "fever_cases_nearby":  cluster["fever"],
            "elevated":            elevated,
            "message":             cluster_msg,
        },
        "historical_by_year": hist_by_year,
        "historical_message": hist_msg,
        "live_alerts_summary": live_summary,
        "summary": summary,
    }

# ── ENDPOINTS ─────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "name": "HealthMind API",
        "version": "2.0.0",
        "endpoints": {
            "GET  /health":        "API status",
            "GET  /cases":         "List all cases",
            "GET  /cases/{id}":    "Get one case",
            "POST /report":        "Submit new incident",
            "POST /analyze":       "Analyze a case (3 layers)",
        },
        "disclaimer": "Decision-support tool. Not medical advice.",
    }

@app.get("/health")
def health():
    llm_ok = False
    try:
        llm_client.chat.completions.create(
            model=MODEL,
            messages=[{"role":"user","content":"ping"}],
            max_tokens=5,
        )
        llm_ok = True
    except Exception:
        pass
    _, az_ok = check_azdhs()
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    return {
        "status":          "ok" if llm_ok else "degraded",
        "total_cases":     total,
        "llm_reachable":   llm_ok,
        "azdhs_reachable": az_ok,
        "timestamp":       datetime.datetime.utcnow().isoformat() + "Z",
    }

@app.get("/cases")
def list_cases():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT case_id, date_of_report, domain_human, "
            "domain_animal, domain_env, created_at FROM cases "
            "ORDER BY created_at DESC"
        ).fetchall()
    return {
        "total": len(rows),
        "cases": [dict(r) for r in rows],
    }

@app.get("/cases/{case_id}")
def get_case(case_id: str):
    cid = case_id.upper()
    with get_db() as conn:
        case = conn.execute(
            "SELECT * FROM cases WHERE case_id=?", (cid,)
        ).fetchone()
        if not case:
            raise HTTPException(404, f"{cid} not found.")
        h = conn.execute(
            "SELECT * FROM human_data WHERE case_id=?", (cid,)
        ).fetchone()
        a = conn.execute(
            "SELECT * FROM animal_data WHERE case_id=?", (cid,)
        ).fetchone()
        e = conn.execute(
            "SELECT * FROM environment_data WHERE case_id=?", (cid,)
        ).fetchone()
    return {
        "case":        dict(case),
        "human":       dict(h) if h else None,
        "animal":      dict(a) if a else None,
        "environment": dict(e) if e else None,
    }

@app.post("/report")
def report(req: ReportRequest):
    # Validate at least one domain selected
    if not any([req.domain_human, req.domain_animal, req.domain_environment]):
        raise HTTPException(400, "Select at least one domain.")

    cid = next_case_id()
    now = datetime.datetime.utcnow().isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO cases VALUES (?,?,?,?,?,?)",
            (cid, now[:10],
             "YES" if req.domain_human else "NO",
             "YES" if req.domain_animal else "NO",
             "YES" if req.domain_environment else "NO",
             now)
        )
        if req.domain_human and req.human:
            h = req.human
            conn.execute("""
                INSERT INTO human_data
                (case_id, age, sex, email, occupation, postal_code,
                 phone_number, household_member_id, geographical_coordinates,
                 date_of_illness, no_symptoms, symptoms, cough_congestion,
                 nauseas_vomiting, difficulty_breathing, sore_throat, rash,
                 fever, chills, diarrhea, bleeding_from_body_openings,
                 red_eyes, muscle_or_body_aches_and_pains,
                 discolored_or_bloody_urine, loss_of_smell_or_taste,
                 yellow_skin_yellow_eyes, absent_from_work, absent_from_school,
                 sought_healthcare, attend_mass_gathering, tick_or_insect_bite,
                 animal_bite, history_of_travel, contact_live_animals,
                 contact_dead_sick_animals, contact_sick_individual,
                 digital_biomarker_signal, photo, diagnostic_lab_confirmation,
                 other_notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (cid, h.age, h.sex, h.email, h.occupation, h.postal_code,
                  h.phone_number, h.household_member_id,
                  h.geographical_coordinates, h.date_of_illness,
                  h.no_symptoms, h.symptoms, h.cough_congestion,
                  h.nauseas_vomiting, h.difficulty_breathing, h.sore_throat,
                  h.rash, h.fever, h.chills, h.diarrhea,
                  h.bleeding_from_body_openings, h.red_eyes,
                  h.muscle_or_body_aches_and_pains,
                  h.discolored_or_bloody_urine, h.loss_of_smell_or_taste,
                  h.yellow_skin_yellow_eyes, h.absent_from_work,
                  h.absent_from_school, h.sought_healthcare,
                  h.attend_mass_gathering, h.tick_or_insect_bite,
                  h.animal_bite, h.history_of_travel, h.contact_live_animals,
                  h.contact_dead_sick_animals, h.contact_sick_individual,
                  h.digital_biomarker_signal, h.photo,
                  h.diagnostic_lab_confirmation, h.other_notes))

        if req.domain_animal and req.animal:
            a = req.animal
            conn.execute("""
                INSERT INTO animal_data
                (case_id, wildlife_date, wildlife_location, wildlife_species,
                 wildlife_dead_count, livestock_date, livestock_location,
                 livestock_species, livestock_sick_count, livestock_dead_count,
                 other_notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (cid, a.wildlife_date, a.wildlife_location, a.wildlife_species,
                  a.wildlife_dead_count, a.livestock_date, a.livestock_location,
                  a.livestock_species, a.livestock_sick_count,
                  a.livestock_dead_count, a.other_notes))

        if req.domain_environment and req.environment:
            e = req.environment
            conn.execute("""
                INSERT INTO environment_data
                (case_id, date_of_incident, location_of_vector_spotting,
                 unusual_presence_of_vectors, density_of_vectors,
                 flooding, water_contamination, other_notes)
                VALUES (?,?,?,?,?,?,?,?)
            """, (cid, e.date_of_incident, e.location_of_vector_spotting,
                  e.unusual_presence_of_vectors, e.density_of_vectors,
                  e.flooding, e.water_contamination, e.other_notes))

    return {
        "case_id":    cid,
        "status":     "saved",
        "domains": {
            "human":       req.domain_human,
            "animal":      req.domain_animal,
            "environment": req.domain_environment,
        },
        "created_at": now,
    }

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    cid = req.case_id.upper()
    with get_db() as conn:
        case = conn.execute(
            "SELECT * FROM cases WHERE case_id=?", (cid,)
        ).fetchone()
        if not case:
            raise HTTPException(404, f"{cid} not found.")
        h = conn.execute(
            "SELECT * FROM human_data WHERE case_id=?", (cid,)
        ).fetchone()
        a = conn.execute(
            "SELECT * FROM animal_data WHERE case_id=?", (cid,)
        ).fetchone()
        e = conn.execute(
            "SELECT * FROM environment_data WHERE case_id=?", (cid,)
        ).fetchone()

    h = dict(h) if h else None
    a = dict(a) if a else None
    e = dict(e) if e else None
    domains = []
    if case["domain_human"]  == "YES": domains.append("human")
    if case["domain_animal"] == "YES": domains.append("animal")
    if case["domain_env"]    == "YES": domains.append("environment")

    # Layer 1
    l1_human  = run_layer1_human(h)  if "human"       in domains else None
    l1_animal = run_layer1_animal(a) if "animal"       in domains else None
    l1_env    = run_layer1_env(e)    if "environment"  in domains else None
    overall   = max(
        [x["severity"] for x in [l1_human, l1_animal, l1_env] if x],
        key=lambda s: {"HIGH":2,"MODERATE":1,"LOW":0}.get(s,0),
        default="LOW"
    )

    # Layer 2
    l2 = run_layer2(domains, l1_human, l1_animal, l1_env, h, a, e)

    # Layer 3
    date_str = (h.get("date_of_illness") if h else None) or case["date_of_report"]
    l3 = run_layer3(cid, date_str, domains=domains)

    return {
        "case_id":      cid,
        "analyzed_at":  datetime.datetime.utcnow().isoformat() + "Z",
        "domains_reported": {
            "human":       "human"       in domains,
            "animal":      "animal"      in domains,
            "environment": "environment" in domains,
        },
        "patient":     h,
        "animal":      a,
        "environment": e,
        "layer1": {
            "human":            l1_human,
            "animal":           l1_animal,
            "environment":      l1_env,
            "overall_severity": overall,
        },
        "layer2": l2,
        "layer3": l3,
        "disclaimer": "Decision-support tool. Not medical or veterinary advice.",
    }
