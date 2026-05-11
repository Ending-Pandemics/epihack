# Data Sources

Curated open datasets for EpiHack Arizona teams. All sources below are freely accessible. Disease surveillance, mobility, environmental, humanitarian, and One Health data — pick what fits your prototype.

> The most current version of this list lives at https://arizona.epihack.org/. If you spot a broken link or want to add a source, open a Pull Request against this file.

## Surveillance

### CDC Travelers' Health & Disease Notices
**Tags:** travel · outbreak alerts
Real-time travel health notices, outbreak alerts by destination, and disease-specific advisories. Useful for mapping geographic risk zones and correlating travel patterns with outbreak signals.
🔗 https://wwwnc.cdc.gov/travel

### CDC FluView Interactive
**Tags:** ILI · sentinel data
Weekly influenza-like illness surveillance from outpatient networks, hospitals, and labs. Regional breakdowns, positivity rates, and time-series back to 1997.
🔗 https://www.cdc.gov/fluview/interactive

### PAHO PLISA — Health Information Platform for the Americas
**Tags:** regional · API
Pan American Health Organization's open data covering disease incidence, vaccination coverage, and outbreak indicators across 52 countries in the Americas. Queryable by disease, country, and year. JSON and CSV outputs.
🔗 https://www.paho.org/data

## Mobility

### Airline Routes & Flight Timetables (OAG · OpenFlights)
Global airline route networks, seat capacity by route, and historic flight schedules. Useful for modelling pandemic spread pathways and estimating cross-border exposure from infectious regions.
🔗 https://github.com/jpatokal/openflights

## Environment

### Weather Data & Vector Habitat Indices (NOAA · NASA EarthData)
Temperature, precipitation, humidity, and NDVI data correlated with mosquito and tick habitat suitability. Use to model *Aedes aegypti* range, dengue and West Nile seasonality, and climate-driven vector risk.
🔗 https://www.earthdata.nasa.gov

## Humanitarian

### Humanitarian Data Exchange — HDX (OCHA)
UN-backed open platform with 19,000+ datasets from 250+ organisations: health, population, infrastructure, displacement, cholera outbreaks, vaccination coverage, and health facility maps.
🔗 https://data.humdata.org

## Global Disease

### WHO Global Health Observatory
WHO's open data repository covering mortality, morbidity, immunisation, and disease-specific indicators across 194 member states. REST API with JSON and CSV outputs.
🔗 https://www.who.int/data/gho

## Early Warning

### ProMED — Program for Monitoring Emerging Diseases (ISID)
One of the oldest infectious disease outbreak reporting systems. Curated outbreak reports from a global network of experts, available via RSS and data API for real-time signal mining.
🔗 https://promedmail.org

## One Health

### GBIF — Global Biodiversity Information Facility
Over 2 billion species occurrence records including vectors, reservoir hosts, and wildlife. Useful for One Health modelling — map where disease reservoirs overlap with human populations and climate shifts.
🔗 https://www.gbif.org/developer

## Social Media Surveillance

### Episomer — ECDC Early Warning Tool
Launched by ECDC in May 2026, Episomer monitors Bluesky posts by time, place, and topic to detect early signals of public health threats. Successor to *epitweetr*. Includes an interactive web app for signal review. Open-source R package.
🔗 https://www.ecdc.europa.eu/episomer

---

## Adding a new source

Open a PR against this file with:
- Category (surveillance, mobility, environment, etc.)
- Name and one-sentence description of what it offers
- Link to documentation or download
- Any access notes (free, registration required, API key, rate limits)
