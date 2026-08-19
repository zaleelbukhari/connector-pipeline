import os
import csv
import sys
import subprocess
import concurrent.futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, OUTPUT_DIR

def get_email_provider(domain: str) -> str:
    """
    Uses native Windows nslookup to resolve MX records and determine the email provider.
    """
    if not domain:
        return "Unknown"
        
    # Clean the domain so nslookup doesn't break
    clean_domain = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "")
    if '/' in clean_domain:
        clean_domain = clean_domain.split('/')[0]
        
    try:
        # Run nslookup
        output = subprocess.check_output(
            ['nslookup', '-type=mx', clean_domain], 
            text=True, 
            stderr=subprocess.STDOUT,
            timeout=5
        )
        
        output_lower = output.lower()
        
        if 'google.com' in output_lower or 'googlemail.com' in output_lower:
            return 'Google Workspace'
        elif 'protection.outlook.com' in output_lower or 'outlook.com' in output_lower:
            return 'Microsoft 365'
        else:
            return 'Other'
    except subprocess.TimeoutExpired:
        return 'Timeout'
    except Exception:
        return 'Unknown'

def get_article(role: str) -> str:
    """Returns 'a' or 'an' depending on if the role starts with a vowel."""
    # Handle acronyms like 'SRE' or 'SDET' which are pronounced with vowel sounds
    upper_acronyms = ["SRE", "SDET", "VP", "HR", "IT"]
    first_word = role.split()[0]
    
    if first_word.upper() in upper_acronyms:
        if first_word.upper() in ["SRE", "SDET", "HR", "IT"]:
            return "an"
        return "a"
        
    if role.lower().startswith(('a', 'e', 'i', 'o', 'u')):
        return "an"
    return "a"

def generate_body(name: str, ats_role: str, company: str) -> str:
    """Generates the ultra-short demand side copy (Option C)."""
    article = get_article(ats_role)
    template = f"{name}, saw {company} is hiring {article} {ats_role}.\n\nI know a recruiter who specializes in sourcing for this exact stack. Want a quick intro to see if they can help fill the seat?"
    return template

def main():
    print("=" * 60)
    print("  DEMAND CAMPAIGN GENERATOR & MX CHECKER")
    print("=" * 60)

    # File paths
    apollo_file = os.path.join(DATA_DIR, "apollo_demand_leads.csv")
    ats_roles_file = os.path.join(OUTPUT_DIR, "ats_enriched_roles.csv")
    output_file = os.path.join(OUTPUT_DIR, "final_demand_campaign.csv")

    # Ensure Apollo dummy file exists if missing
    if not os.path.exists(apollo_file):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(apollo_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["First Name", "Email", "Company Domain"])
            writer.writerow(["John", "john@outset.ai", "outset.ai"])
            writer.writerow(["Sarah", "sarah@airbnb.com", "airbnb.com"])
        print(f"[!] Created a template Apollo file at: {apollo_file}")

    # Load ATS Roles
    ats_mapping = {}
    if os.path.exists(ats_roles_file):
        with open(ats_roles_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                domain = row.get("domain", "").strip().lower()
                domain = domain.replace("http://", "").replace("https://", "").replace("www.", "")
                if '/' in domain:
                    domain = domain.split('/')[0]
                role = row.get("casual_role", "").strip()
                if domain and role:
                    ats_mapping[domain] = role
    else:
        print("[!] Warning: ats_enriched_roles.csv not found. Roles will default to 'senior engineering'.")

    # Read Apollo Leads
    leads = []
    with open(apollo_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        
        # Try to dynamically find columns
        name_col = next((c for c in reader.fieldnames if "name" in c.lower() and "last" not in c.lower() and "company" not in c.lower()), None)
        email_col = next((c for c in reader.fieldnames if c.strip().lower() == "email"), None)
        domain_col = next((c for c in reader.fieldnames if "domain" in c.lower() or "website" in c.lower()), None)
        company_col = next((c for c in reader.fieldnames if "company" in c.lower() and "domain" not in c.lower() and "website" not in c.lower()), None)
        
        if not all([name_col, email_col, domain_col]):
            print("[!] Could not automatically map Apollo columns.")
            print(f"    Found columns: {reader.fieldnames}")
            print("    Looking for: First Name, Email, Company Domain")
            return
            
        for row in reader:
            raw_name = row[name_col].strip()
            first_name = raw_name.split()[0] if raw_name else ""
            
            raw_domain = row[domain_col].strip().lower()
            clean_domain = raw_domain.replace("http://", "").replace("https://", "").replace("www.", "")
            if '/' in clean_domain:
                clean_domain = clean_domain.split('/')[0]
                
            company_name = ""
            if company_col and row.get(company_col):
                company_name = row[company_col].strip().lower()
            else:
                # Fallback to domain prefix
                company_name = clean_domain.split('.')[0].lower()
            
            leads.append({
                "name": first_name,
                "email": row[email_col].strip(),
                "domain": clean_domain,
                "company": company_name
            })

    print(f"Loaded {len(leads)} leads. Checking MX records concurrently...")

    # Process concurrently
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # Create a future for each lead
        future_to_lead = {executor.submit(get_email_provider, lead["domain"]): lead for lead in leads}
        
        completed = 0
        for future in concurrent.futures.as_completed(future_to_lead):
            lead = future_to_lead[future]
            provider = "Unknown"
            try:
                provider = future.result()
            except Exception as exc:
                print(f"Domain {lead['domain']} generated an exception: {exc}")
                
            completed += 1
            # Get the ATS role
            role = ats_mapping.get(lead["domain"])
            
            # STRICT FILTER: If no active engineering role was found, we skip them entirely
            if not role or role.strip() == "":
                print(f"[{completed}/{len(leads)}] {lead['domain']} -> Skipped (No active ATS role)")
                continue
                
            safe_role = role.encode('ascii', 'ignore').decode()
            print(f"[{completed}/{len(leads)}] {lead['domain']} -> {provider} (Role: {safe_role})")
            
            # Generate the body
            body = generate_body(lead["name"], role, lead["company"])
            
            results.append({
                "Email": lead["email"],
                "First Name": lead["name"],
                "Domain": lead["domain"],
                "Provider": provider,
                "Subject": "", # Leave blank for Reachinbox / Instantly
                "Body": body
            })

    # Write final output
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Email", "First Name", "Domain", "Provider", "Subject", "Body"])
        writer.writeheader()
        writer.writerows(results)

    print("\n" + "=" * 60)
    print("  Campaign Generation Complete!")
    print(f"  Processed {len(results)} leads.")
    print(f"  Output saved to: {output_file}")
    print("=" * 60)

if __name__ == "__main__":
    main()
