import csv

def main():
    input_file = r'd:\Programming\connector-pipeline\data\filtered_indeed_leads.csv'
    output_file = r'd:\Programming\connector-pipeline\data\semicon_only_leads.csv'

    # Keywords that indicate a semiconductor/hardware engineering role
    semicon_keywords = [
        'analog', 'digital', 'ic', 'asic', 'soc', 'mixed-signal', 'rf', 
        'hardware', 'embedded', 'firmware', 'silicon', 'layout', 'rtl', 
        'mask', 'semiconductor', 'fpga', 'microcontroller', 'bsp', 'pcb',
        'system on chip', 'verification', 'validation'
    ]

    # Keywords to explicitly exclude (like product manager, marketing, etc.)
    exclude_keywords = [
        'product manager', 'marketing', 'sales', 'account executive', 
        'hr', 'human resources', 'recruiter', 'software engineer', 
        'front end', 'backend', 'full stack', 'web'
    ]

    print("Filtering for semiconductor-specific roles...")
    
    with open(input_file, 'r', encoding='utf-8') as infile, \
         open(output_file, 'w', encoding='utf-8', newline='') as outfile:
        
        reader = csv.DictReader(infile)
        
        # Write headers
        writer = csv.DictWriter(outfile, fieldnames=reader.fieldnames)
        writer.writeheader()
        
        total = 0
        kept = 0
        
        for row in reader:
            total += 1
            title = row.get('title', '').lower()
            
            # 1. Check if it contains any exclude keywords
            if any(excl in title for excl in exclude_keywords):
                continue
                
            # 2. Check if it contains any semicon keywords
            # For short keywords like 'ic' or 'rf', we want to make sure it's a distinct word
            # to avoid matching "policy" or "perfect"
            
            # A quick helper to check distinct words for short acronyms
            words = title.split()
            # Clean punctuation from words
            words = [w.strip('-,.()') for w in words]
            
            has_semicon = False
            for kw in semicon_keywords:
                if len(kw) <= 3:
                    if kw in words:
                        has_semicon = True
                        break
                else:
                    if kw in title:
                        has_semicon = True
                        break
                        
            if has_semicon:
                writer.writerow(row)
                kept += 1

    print(f"Done! Evaluated {total} rows. Kept {kept} highly-targeted semiconductor leads.")
    print(f"Saved to: {output_file}")

if __name__ == '__main__':
    main()
