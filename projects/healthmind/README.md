# HealthMind — One Health Query System

A 3-layer One Health incident reporting and analysis system built at EpiHack Arizona 2026.

## What it does
- Users report incidents across Human, Animal, and Environment domains
- 3-layer analysis:
  - Layer 1: Incident classification + severity
  - Layer 2: Contextual risk advisory (Arizona-grounded)
  - Layer 3: Live health alerts + historical comparison
- Supports all 7 domain combinations

## Tech Stack
- Backend: FastAPI + SQLite
- LLM: Llama-3.3-70B via CyVerse
- Frontend: Single-file HTML dashboard

## How to run
1. pip install -r src/requirements.txt
2. export LLAMA_API_KEY="your-key"
3. cd src && uvicorn api:app --reload
4. Open src/healthmind.html in browser

## API Endpoints
- POST /report  → submit incident, get case ID
- POST /analyze → 3-layer analysis
- GET  /docs    → interactive API docs

## Team
University of Arizona Global Health Institute — EpiHack Arizona 2026
