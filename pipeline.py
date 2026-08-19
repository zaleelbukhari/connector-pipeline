"""
Pipeline Orchestrator
Runs all scrapers, scores companies, and generates the final outreach CSV.

Usage:
    python pipeline.py              # Run full pipeline
    python pipeline.py scrape       # Run only scrapers
    python pipeline.py score        # Run only scoring (after scrapers)
    python pipeline.py generate     # Run only CSV generation (after scoring)
    python pipeline.py --help       # Show help
"""

import sys
import os
import io
import time
import importlib

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, OUTPUT_DIR


def run_scrapers():
    """Run all scrapers and collect data into data/ directory."""
    print("\n" + "=" * 60)
    print("  PHASE 1: SCRAPING - Collecting data from all sources")
    print("=" * 60)

    scrapers = [
        ("HN Who's Hiring", "scrapers.hn_who_is_hiring"),
        ("RemoteOK", "scrapers.remoteok"),
        ("WeWorkRemotely", "scrapers.weworkremotely"),
        ("SEC EDGAR Form D", "scrapers.edgar_funding"),
    ]

    results = {}
    for name, module_path in scrapers:
        print(f"\n{'-' * 40}")
        print(f"  Running: {name}")
        print(f"{'-' * 40}")

        try:
            module = importlib.import_module(module_path)
            df = module.main()
            count = len(df) if df is not None else 0
            results[name] = count
            print(f"  [OK] {name}: {count} records collected")
        except Exception as e:
            print(f"  [FAIL] {name} FAILED: {e}")
            results[name] = 0

    print(f"\n{'=' * 60}")
    print("  SCRAPING COMPLETE - Summary")
    print(f"{'=' * 60}")
    for name, count in results.items():
        status = "[OK]" if count > 0 else "[FAIL]"
        print(f"  {status} {name}: {count} records")
    total = sum(results.values())
    print(f"\n  Total records collected: {total}")
    print(f"  Data saved to: {DATA_DIR}")

    return results


def run_scoring():
    """Cross-reference and score companies."""
    print("\n" + "=" * 60)
    print("  PHASE 2: SCORING - Cross-referencing and scoring companies")
    print("=" * 60)

    # Check if data files exist
    data_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
    if not data_files:
        print("  [FAIL] No data files found in data/. Run scrapers first.")
        print("    Usage: python pipeline.py scrape")
        return None

    print(f"  Found {len(data_files)} data files: {', '.join(data_files)}")

    try:
        from processing.score_companies import main as score_main
        df = score_main()
        if df is not None:
            print(f"\n  [OK] Scoring complete. Results saved to output/scored_companies.csv")
        return df
    except Exception as e:
        print(f"  [FAIL] Scoring FAILED: {e}")
        import traceback
        traceback.print_exc()
        return None


def run_generation():
    """Generate the Reachinbox-ready outreach CSV."""
    print("\n" + "=" * 60)
    print("  PHASE 3: GENERATION - Building outreach CSV")
    print("=" * 60)

    scored_file = os.path.join(OUTPUT_DIR, "scored_companies.csv")
    if not os.path.exists(scored_file):
        print("  [FAIL] scored_companies.csv not found. Run scoring first.")
        print("    Usage: python pipeline.py score")
        return None

    enriched_file = os.path.join(OUTPUT_DIR, "enriched_contacts.csv")
    if os.path.exists(enriched_file):
        print(f"  Found enriched_contacts.csv - will merge with scored data")
    else:
        print(f"  No enriched_contacts.csv found - using scraped contacts only")
        print(f"  (To add Apollo contacts, place enriched_contacts.csv in output/)")

    try:
        from processing.generate_outreach_csv import main as gen_main
        df = gen_main()
        if df is not None:
            print(f"\n  [OK] Outreach CSV generated. Ready for Reachinbox upload.")
            print(f"    File: output/outreach_ready.csv")
            print(f"    Contacts: {len(df)}")
        return df
    except Exception as e:
        print(f"  [FAIL] Generation FAILED: {e}")
        import traceback
        traceback.print_exc()
        return None


def print_help():
    """Print usage instructions."""
    print("""
--------------------------------------------------------------
           CONNECTOR PIPELINE - Demand-Side Sourcing         
--------------------------------------------------------------
                                                              
  This pipeline scrapes niche hiring sources, cross-          
  references companies across them, scores by pain            
  intensity, and generates a Reachinbox-ready CSV with        
  personalized outreach copy.                                 
                                                              
  USAGE:                                                      
    python pipeline.py              Run full pipeline          
    python pipeline.py scrape       Run scrapers only          
    python pipeline.py score        Score companies only       
    python pipeline.py generate     Generate outreach CSV      
    python pipeline.py --help       Show this help             
                                                              
  WORKFLOW:                                                   
    1. Run scrapers (auto or manual Apify actors)             
    2. Score and cross-reference companies                    
    3. (Optional) Add Apollo contacts to output/              
       enriched_contacts.csv                                  
    4. Generate outreach CSV                                  
    5. Upload outreach_ready.csv to Reachinbox                
                                                              
  MANUAL DATA (place in data/ directory):                     
    - greenhouse.csv    (from Apify Greenhouse actor)         
    - lever.csv         (from Apify Lever actor)              
    - wellfound.csv     (from Apify Wellfound actor)          
                                                              
  MANUAL CONTACTS (place in output/ directory):               
    - enriched_contacts.csv  (from Apollo enrichment)         
      Columns: email, first_name, last_name, title,           
               company_name                                   
                                                              
--------------------------------------------------------------
""")


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    command = args[0].lower()

    start = time.time()

    if command == "--help" or command == "-h" or command == "help":
        print_help()
        return

    if command == "all":
        print("\n>> Starting full pipeline...\n")
        run_scrapers()
        run_scoring()
        run_generation()

    elif command == "scrape":
        run_scrapers()

    elif command == "score":
        run_scoring()

    elif command == "generate":
        run_generation()

    else:
        print(f"Unknown command: {command}")
        print_help()
        return

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"  Pipeline completed in {elapsed:.1f} seconds")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
