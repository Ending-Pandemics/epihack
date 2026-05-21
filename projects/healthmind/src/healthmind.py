#!/usr/bin/env python3
"""
HealthMind - One Health 3-Layer Query System (Proof of Concept)
Reads from epihack.db (4-table structure).

Usage:  python healthmind.py
  - Enter case IDs: HM-001 .. HM-NNN
  - Type 'done' to finish and see session summary

Requirements:
  pip install openai requests
Environment:
  export LLAMA_API_KEY="your-cyverse-key"
"""

import os, sys, sqlite3, datetime, requests
from contextlib import contextmanager
from openai import OpenAI

# ── CONFIG ───────────────────────────────────────────────────────────
DB_PATH  = os.environ.get("DATABASE_URL", "epihack.db")
BASE_URL = "https://llm-api.cyverse.ai/v1"
MODEL    = "llama-3.3-70b-instruct-quantized"
API_KEY  = os.environ.get("LLAMA_API_KEY")
WINDOW   = 7

AZDHS_URL = "https://www.azdhs.gov/preparedness/epidemiology-disease-control/index.php"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
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
- Concurrent human + animal + environmental signals: possible zoonotic spillover.
- Report to AZDHS when a reportable condition is suspected.
"""

# ── DATABASE ─────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def load_case(cid):
    with get_db() as conn:
        case = conn.execute(
            "SELECT * FROM cases WHERE case_id=?", (cid,)
        ).fetchone()
        if not case:
            return None, None, None, None
        h = conn.execute(
            "SELECT * FROM human_data WHERE case_id=?", (cid,)
        ).fetchone()
        a = conn.execute(
            "SELECT * FROM animal_data WHERE case_id=?", (cid,)
        ).fetchone()
        e = conn.execute(
            "SELECT * FROM environment_data WHERE case_id=?", (cid,)
        ).fetchone()
    return dict(case), dict(h) if h else None, dict(a) if a else None, dict(e) if e else None

def list_all_cases():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT case_id FROM cases ORDER BY created_at"
        ).fetchall()
    return [r["case_id"] for r in rows]

# ── LLM ──────────────────────────────────────────────────────────────
def ask_llm(client, system, user):
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            temperature=0.3, max_tokens=500,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"[LLM error: {e}]"

# ── LAYER 1 ──────────────────────────────────────────────────────────
def layer1_human(client, h):
    if not h:
        return None
    sx     = [c for c in SYMPTOM_COLS if str(h.get(c,"")).upper() == "YES"]
    severe = [c for c in sx if c in SEVERE_MARKERS]
    sev    = "HIGH" if severe else ("MODERATE" if sx else "LOW")
    facts  = (f"Human: {h.get('age')}yo {h.get('sex')}, "
              f"{h.get('occupation')}. "
              f"Illness: {h.get('date_of_illness')}. "
              f"Symptoms: {', '.join(sx) or 'none'}. "
              f"Severe: {', '.join(severe) or 'none'}.")
    narrative = ask_llm(client,
        "You are a One Health incident interpreter. Restate in 2-3 plain "
        "sentences. Do not diagnose. End with severity level.",
        facts + f"\nSeverity: {sev}")
    return {"symptoms": sx, "severe_markers": severe,
            "severity": sev, "narrative": narrative}

def layer1_animal(client, a):
    if not a:
        return None
    facts = (f"Animal: Wildlife — {a.get('wildlife_species')} at "
             f"{a.get('wildlife_location')}, {a.get('wildlife_dead_count')} dead. "
             f"Livestock — {a.get('livestock_species')} at "
             f"{a.get('livestock_location')}, {a.get('livestock_sick_count')} sick, "
             f"{a.get('livestock_dead_count')} dead.")
    dead = (a.get("wildlife_dead_count") or 0) + (a.get("livestock_dead_count") or 0)
    sick = a.get("livestock_sick_count") or 0
    sev  = "HIGH" if dead >= 8 else ("MODERATE" if (dead+sick) > 3 else "LOW")
    narrative = ask_llm(client,
        "You are a One Health incident interpreter. Restate this animal "
        "incident in 2-3 plain sentences. End with severity level.",
        facts + f"\nSeverity: {sev}")
    return {"severity": sev, "narrative": narrative}

def layer1_env(client, e):
    if not e:
        return None
    facts = (f"Environment: vectors at {e.get('location_of_vector_spotting')} "
             f"on {e.get('date_of_incident')}. "
             f"Unusual: {e.get('unusual_presence_of_vectors')}, "
             f"density: {e.get('density_of_vectors')}. "
             f"Flooding: {e.get('flooding')}, "
             f"water: {e.get('water_contamination')}.")
    density = str(e.get("density_of_vectors","")).upper()
    unusual = str(e.get("unusual_presence_of_vectors","")).upper()
    sev = ("HIGH"     if density == "HIGH" and unusual == "YES" else
           "MODERATE" if unusual == "YES" else "LOW")
    narrative = ask_llm(client,
        "You are a One Health incident interpreter. Restate this environmental "
        "incident in 2-3 plain sentences. End with severity level.",
        facts + f"\nSeverity: {sev}")
    return {"severity": sev, "narrative": narrative}

# ── LAYER 2 ──────────────────────────────────────────────────────────
def layer2(client, domains, l1h, l1a, l1e):
    parts = []
    if l1h: parts.append(f"Human symptoms: {l1h['symptoms']}, severity {l1h['severity']}.")
    if l1a: parts.append(f"Animal: {l1a['narrative']}")
    if l1e: parts.append(f"Environment: {l1e['narrative']}")
    advisory = ask_llm(client,
        "You are a One Health risk advisor. Using ONLY the reference context, "
        "give a 3-4 sentence advisory covering all reported domains. "
        "Highlight One Health connections if multiple domains. "
        "State this is decision-support, not medical advice.",
        f"Reference:\n{AZ_REFERENCE}\n\n"
        f"Domains: {', '.join(domains)}.\n" + "\n".join(parts))
    return advisory

# ── LAYER 3 ──────────────────────────────────────────────────────────
def check_azdhs():
    try:
        r = requests.get(AZDHS_URL, timeout=15, headers=HTTP_HEADERS)
        return f"AZDHS {'reachable' if r.ok else 'HTTP '+str(r.status_code)}.", r.ok
    except Exception as e:
        return f"AZDHS fetch failed: {e}", False

def layer3(client, cid, date_str):
    src, src_ok = check_azdhs()
    human_c = animal_c = env_c = fever_c = hist_c = hist_f = 0

    if date_str:
        try:
            tdate = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT c.case_id, c.domain_human, c.domain_animal, "
                    "c.domain_env, c.date_of_report, h.fever, h.date_of_illness "
                    "FROM cases c LEFT JOIN human_data h ON c.case_id=h.case_id "
                    "WHERE c.case_id != ?", (cid,)
                ).fetchall()
            for row in rows:
                d_str = row["date_of_illness"] or row["date_of_report"]
                if not d_str: continue
                try:
                    d = datetime.datetime.strptime(d_str[:10], "%Y-%m-%d").date()
                    delta = abs((d - tdate).days)
                    if delta <= WINDOW:
                        if row["domain_human"]  == "YES": human_c += 1
                        if row["domain_animal"] == "YES": animal_c += 1
                        if row["domain_env"]    == "YES": env_c   += 1
                        if str(row["fever"]).upper() == "YES": fever_c += 1
                    # historical — different year, same calendar date
                    if d.year != tdate.year:
                        cal_delta = abs((
                            datetime.date(2000, d.month, d.day) -
                            datetime.date(2000, tdate.month, tdate.day)
                        ).days)
                        if cal_delta <= WINDOW:
                            hist_c += 1
                            if str(row["fever"]).upper() == "YES": hist_f += 1
                except Exception:
                    continue
        except Exception:
            pass

    elevated = fever_c >= 4
    current  = (f"{human_c+animal_c+env_c} incidents within ±{WINDOW} days "
                f"({human_c} human, {animal_c} animal, {env_c} env); "
                f"{fever_c} fever cases."
                + (" ELEVATED: possible outbreak signal." if elevated else ""))
    hist     = (f"{hist_c} cases around same date last year; {hist_f} with fever."
                if hist_c > 0 else "No comparable cases last year for this date.")

    summary = ask_llm(client,
        "You are a One Health live-data summarizer. In 2-3 sentences "
        "summarize live source status, current cluster signal, "
        "and year-over-year comparison.",
        f"Live: {src}\nCurrent: {current}\nHistorical: {hist}")
    return {
        "src": src, "src_ok": src_ok,
        "current": current, "hist": hist,
        "elevated": elevated, "summary": summary
    }

# ── ANALYZE ONE CASE ─────────────────────────────────────────────────
def analyze(client, cid, n):
    case, h, a, e = load_case(cid)
    if not case:
        print(f"  '{cid}' not found in database.")
        return None

    domains = []
    if case["domain_human"]  == "YES": domains.append("human")
    if case["domain_animal"] == "YES": domains.append("animal")
    if case["domain_env"]    == "YES": domains.append("environment")

    print("\n" + "=" * 64)
    print(f"  HEALTHMIND  |  Case #{n}  |  {cid}")
    print(f"  Domains: {', '.join(domains) or 'none'}")
    print("=" * 64)

    # Layer 1
    l1h = layer1_human(client, h)  if "human"       in domains else None
    l1a = layer1_animal(client, a) if "animal"       in domains else None
    l1e = layer1_env(client, e)    if "environment"  in domains else None
    overall = max(
        [x["severity"] for x in [l1h, l1a, l1e] if x],
        key=lambda s: {"HIGH":2,"MODERATE":1,"LOW":0}.get(s,0),
        default="LOW"
    )

    print(f"\n[ LAYER 1 ]  INCIDENT REPORTED  (overall: {overall})")
    if l1h:
        print(f"  HUMAN    | severity: {l1h['severity']}")
        print(f"  symptoms : {', '.join(l1h['symptoms']) or 'none'}")
        print(f"  {l1h['narrative']}")
    if l1a:
        print(f"  ANIMAL   | severity: {l1a['severity']}")
        print(f"  {l1a['narrative']}")
    if l1e:
        print(f"  ENV      | severity: {l1e['severity']}")
        print(f"  {l1e['narrative']}")

    print("\n[ LAYER 2 ]  CONTEXTUAL RISK ADVISORY")
    print(f"  {layer2(client, domains, l1h, l1a, l1e)}")

    print("\n[ LAYER 3 ]  LIVE DATA & HISTORICAL SIGNAL")
    date_str = (h.get("date_of_illness") if h else None) or case["date_of_report"]
    l3 = layer3(client, cid, date_str)
    print(f"  {l3['summary']}")
    print(f"\n  Current : {l3['current']}")
    print(f"  History : {l3['hist']}")

    return {"id": cid, "domains": domains, "overall_severity": overall,
            "symptoms": l1h["symptoms"] if l1h else []}

# ── SESSION SUMMARY ──────────────────────────────────────────────────
def session_summary(analyzed):
    print("\n" + "#" * 64)
    print(f"  SESSION SUMMARY  —  {len(analyzed)} case(s)")
    print("#" * 64)
    sev = {"HIGH":0,"MODERATE":0,"LOW":0}
    for a in analyzed:
        sev[a["overall_severity"]] = sev.get(a["overall_severity"],0) + 1
        print(f"  {a['id']:9s} | {','.join(a['domains']):25s} | "
              f"{a['overall_severity']}")
    print("-" * 64)
    print(f"  HIGH: {sev['HIGH']}  MODERATE: {sev['MODERATE']}  LOW: {sev['LOW']}")
    print("#" * 64)
    print("  Decision-support tool. Not medical or veterinary advice.")
    print("#" * 64)

# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        print("ERROR: set LLAMA_API_KEY first.")
        sys.exit(1)
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found. Run api.py first to create it.")
        sys.exit(1)

    all_ids  = list_all_cases()
    client   = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    print("=" * 64)
    print("  HEALTHMIND  —  One Health Query System")
    print("=" * 64)
    print(f"  {len(all_ids)} cases in database.")
    print(f"  Range: {all_ids[0]} .. {all_ids[-1]}")
    print("  Type a case ID to analyze, or 'done' to finish.\n")

    analyzed = []
    while True:
        raw = input(f"\n>>> Case ID #{len(analyzed)+1} "
                    f"(or 'done'): ").strip().upper()
        if raw in ("DONE","QUIT","EXIT","Q",""):
            break
        result = analyze(client, raw, len(analyzed)+1)
        if result:
            analyzed.append(result)
            print(f"\n  ✓ Done. Enter next case ID or 'done'.")

    if analyzed:
        session_summary(analyzed)
    else:
        print("\n  No cases analyzed. Goodbye.")

if __name__ == "__main__":
    main()
