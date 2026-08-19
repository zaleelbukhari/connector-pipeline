import csv
import sys
import urllib.parse

def extract_domain(url):
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        # Handle cases where url might not have scheme
        if not parsed.netloc:
            parsed = urllib.parse.urlparse("http://" + url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except:
        return url

def main():
    input_file = r'D:\Downloads\mightymerge.io__v4qqmyir.csv'
    output_file = r'D:\Programming\connector-pipeline\data\filtered_indeed_leads.csv'

    useful_columns = [
        'companyDetails/name',
        'companyDetails/websiteUrl',
        'title',
        'companyDetails/employeeRange',
        'companyDetails/industry',
        'companyDetails/revenue',
        'jobLocationCity',
        'jobLocationState',
        'salary/min',
        'salary/max',
        'originalApplyUrl'
    ]

    megacap_indicators = ['10,000+', '5,001 to 10,000', 'more than $10B (USD)', '$5B to $10B (USD)']

    print("Processing CSV...")
    
    # We use utf-8 with replace to handle any bad chars
    with open(input_file, 'r', encoding='utf-8', errors='replace') as infile, \
         open(output_file, 'w', encoding='utf-8', newline='', errors='replace') as outfile:
        
        # Skip the first 4 lines
        for _ in range(4):
            next(infile)
            
        reader = csv.reader(infile)
        writer = csv.writer(outfile)
        
        try:
            headers = next(reader)
        except StopIteration:
            print("Empty file")
            return
            
        # Find indices of useful columns
        col_indices = []
        out_headers = []
        for col in useful_columns:
            if col in headers:
                col_indices.append(headers.index(col))
                # rename the output header to be cleaner
                out_headers.append(col.split('/')[-1])
        
        out_headers[out_headers.index('websiteUrl')] = 'domain'
        writer.writerow(out_headers)
        
        website_idx = headers.index('companyDetails/websiteUrl') if 'companyDetails/websiteUrl' in headers else -1
        emp_idx = headers.index('companyDetails/employeeRange') if 'companyDetails/employeeRange' in headers else -1
        rev_idx = headers.index('companyDetails/revenue') if 'companyDetails/revenue' in headers else -1
        
        written = 0
        total = 0
        
        for row in reader:
            total += 1
            if len(row) < len(headers):
                continue
                
            emp_range = row[emp_idx] if emp_idx != -1 else ""
            revenue = row[rev_idx] if rev_idx != -1 else ""
            
            # Filter out megacaps
            if any(ind in emp_range for ind in megacap_indicators) or any(ind in revenue for ind in megacap_indicators):
                continue
                
            # Filter out if company name is missing
            company_name_idx = headers.index('companyDetails/name') if 'companyDetails/name' in headers else -1
            if company_name_idx == -1 or not row[company_name_idx].strip():
                continue
                
            # Keep rows with domain or something we can reach out to
            if website_idx != -1:
                original_url = row[website_idx]
                row[website_idx] = extract_domain(original_url)
                
            out_row = [row[i] for i in col_indices]
            writer.writerow(out_row)
            written += 1
            
            if total % 100000 == 0:
                print(f"Processed {total} rows, kept {written}...")

    print(f"Done. Processed {total} rows. Kept {written} useful leads.")
    print(f"Output saved to {output_file}")

if __name__ == '__main__':
    main()
