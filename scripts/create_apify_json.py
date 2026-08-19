import csv
import json

def main():
    input_file = r'd:\Programming\connector-pipeline\data\semicon_only_leads.csv'
    output_file = r'd:\Programming\connector-pipeline\data\apify_input.json'
    
    unique_domains = set()
    
    # Read the domains from the filtered CSV
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = row.get('domain', '').strip()
            if domain:
                unique_domains.add(domain)
                
    domains_list = sorted(list(unique_domains))
    
    # The decision-maker job titles we identified earlier
    decision_maker_titles = [
        "VP of Engineering",
        "Vice President of Engineering",
        "VP of Hardware",
        "VP of Hardware Engineering",
        "VP of Silicon",
        "Chief Technology Officer",
        "CTO",
        "Head of Engineering",
        "Head of Hardware",
        "Director of Engineering",
        "Director of Hardware",
        "Director of Hardware Engineering",
        "Director of ASIC Design",
        "Director of Silicon",
        "Engineering Manager",
        "Hardware Engineering Manager"
    ]
    
    # Construct the JSON structure
    apify_json = {
        "roleMatchMode": "any",
        "companyMatchMode": "any",
        "companyKeywordMode": "broad",
        "companyDomainMatchMode": "strict",
        "personTitleIncludes": decision_maker_titles,
        "personLocationCountryIncludes": [
            "United States"
        ],
        "companyDomainIncludes": domains_list,
        "companyLocationCountryIncludes": [
            "United States"
        ]
    }
    
    # Save the JSON file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(apify_json, f, indent=2)
        
    print(f"Successfully generated Apify JSON with {len(domains_list)} unique domains.")
    print(f"Saved to: {output_file}")

if __name__ == '__main__':
    main()
