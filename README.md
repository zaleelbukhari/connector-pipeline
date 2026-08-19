# Connector Pipeline

Scrapes public hiring sources, scores companies that look like they need recruiting help, and writes an outreach-ready CSV.

Sources:

- Hacker News Who's Hiring
- RemoteOK
- We Work Remotely
- SEC EDGAR Form D (recent funding)

Then scoring, deep-tech ranking, and campaign CSV generation live in `processing/`.

## Setup

```bash
pip install -r requirements.txt
python pipeline.py
```

Useful modes:

```bash
python pipeline.py scrape
python pipeline.py score
python pipeline.py generate
```

`data/` and `output/` are local working folders and are not committed.

ArcSend helper scripts in `scripts/` read `ARCSEND_API_KEY` and `ARCSEND_CAMPAIGN_ID` from the environment. Do not hardcode tokens.
