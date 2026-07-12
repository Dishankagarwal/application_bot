import math
import time
import logging
import pandas as pd
from typing import List, Dict, Any
from jobspy import scrape_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2

def clean_val(v, default=""):
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return default
    return v

def run_job_search(
    search_term: str,
    location: str,
    results_wanted: int = 15,
    site_names: List[str] = None,
    job_type: str = None,
    min_amount: int = None,
    max_amount: int = None,
    hours_old: int = None
) -> List[Dict[str, Any]]:
    """
    Scrapes jobs from multiple job boards using python-jobspy.
    Normalizes the output DataFrame to a JSON-serializable list of dictionaries.
    """
    if not site_names:
        site_names = ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "naukri", "bayt"]
    
    logger.info(f"Searching for '{search_term}' in '{location}' across {site_names} with filters (job_type={job_type}, min_amount={min_amount}, max_amount={max_amount}, hours_old={hours_old})...")
    
    try:
        # Build arguments dictionary dynamically
        scrape_kwargs = {
            "site_name": site_names,
            "search_term": search_term,
            "location": location,
            "results_wanted": results_wanted,
            "country_indeed": 'USA'  # Default country context
        }
        
        if "linkedin" in site_names:
            scrape_kwargs["linkedin_fetch_description"] = True
        
        if job_type:
            scrape_kwargs["job_type"] = job_type
        if min_amount is not None:
            scrape_kwargs["min_amount"] = min_amount
        if max_amount is not None:
            scrape_kwargs["max_amount"] = max_amount
        if hours_old is not None:
            scrape_kwargs["hours_old"] = hours_old
        if min_amount is not None or max_amount is not None:
            scrape_kwargs["enforce_annual_salary"] = True

        # python-jobspy returns a pandas DataFrame — retry with backoff on transient errors
        jobs_df = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                jobs_df = scrape_jobs(**scrape_kwargs)
                break  # Success
            except Exception as retry_err:
                wait = BASE_BACKOFF_SECONDS ** attempt
                logger.warning(f"Scrape attempt {attempt}/{MAX_RETRIES} failed for {site_names}: {retry_err}. Retrying in {wait}s...")
                if attempt == MAX_RETRIES:
                    logger.error(f"All {MAX_RETRIES} scrape attempts exhausted for {site_names}.")
                    raise
                time.sleep(wait)
        
        if jobs_df is None or jobs_df.empty:
            logger.info("No jobs found matching the query.")
            return []
            
        logger.info(f"Scraped {len(jobs_df)} jobs.")
        
        normalized_jobs = []
        for _, row in jobs_df.iterrows():
            # Get job description, handling potential missing columns
            desc = clean_val(row.get("description"), "")
            
            # Format salary if present
            salary = ""
            min_sal = clean_val(row.get("min_amount"), None)
            max_sal = clean_val(row.get("max_amount"), None)
            curr = clean_val(row.get("currency"), "$")
            interval = clean_val(row.get("interval"), "yearly")
            
            if min_sal is not None and max_sal is not None:
                salary = f"{curr}{min_sal:,} - {curr}{max_sal:,} per {interval}"
            elif min_sal is not None:
                salary = f"{curr}{min_sal:,}+ per {interval}"
            elif max_sal is not None:
                salary = f"Up to {curr}{max_sal:,} per {interval}"
                
            job_item = {
                "id": str(clean_val(row.get("id"), "")),
                "site": str(clean_val(row.get("site"), "unknown")),
                "job_url": str(clean_val(row.get("job_url") or row.get("job_url_direct"), "")),
                "title": str(clean_val(row.get("title"), "No Title")),
                "company": str(clean_val(row.get("company"), "Unknown Company")),
                "location": str(clean_val(row.get("location"), "Unknown Location")),
                "date_posted": str(clean_val(row.get("date_posted"), "")),
                "job_type": str(clean_val(row.get("job_type"), "")),
                "is_remote": bool(clean_val(row.get("is_remote"), False)),
                "salary": salary,
                "description": desc
            }
            normalized_jobs.append(job_item)
            
        return enrich_job_descriptions(normalized_jobs)

    except Exception as e:
        logger.error(f"Error during job scraping: {str(e)}", exc_info=True)
        return []


def deduplicate_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicates jobs by normalizing (company, title, location) into a key.
    When duplicates are found, keeps the entry with the longest description
    and merges source platform names.
    """
    import re
    seen = {}  # key -> job dict
    
    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r'[^a-z0-9\s]', '', s)
        s = re.sub(r'\s+', ' ', s)
        return s
    
    for job in jobs:
        key = (
            normalize(job.get("company", "")),
            normalize(job.get("title", "")),
            normalize(job.get("location", ""))
        )
        
        if key in seen:
            existing = seen[key]
            # Keep the one with longer description
            existing_desc_len = len(existing.get("description") or "")
            new_desc_len = len(job.get("description") or "")
            
            # Merge source platform names
            existing_site = existing.get("site", "")
            new_site = job.get("site", "")
            if new_site and new_site not in existing_site:
                existing["site"] = f"{existing_site}, {new_site}"
            
            if new_desc_len > existing_desc_len:
                # Replace with better description but keep merged site
                merged_site = existing["site"]
                seen[key] = job
                seen[key]["site"] = merged_site
            
            logger.info(f"Dedup: Merged duplicate '{job.get('title')}' at '{job.get('company')}' (sources: {seen[key]['site']})")
        else:
            seen[key] = job
    
    deduped = list(seen.values())
    removed = len(jobs) - len(deduped)
    if removed > 0:
        logger.info(f"Deduplication removed {removed} duplicate jobs. {len(deduped)} unique jobs remain.")
    return deduped

def run_gemini_google_search(search_term: str, location: str, results_wanted: int = 15) -> List[Dict[str, Any]]:
    """
    Uses Gemini 2.5 Flash with Google Search Grounding to search for current job openings.
    """
    import os
    import json
    import re
    import uuid
    from google import genai
    from google.genai import types
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not found. Cannot run Gemini Google Search.")
        return []
        
    client = genai.Client(api_key=api_key)
    
    # We formulate a specific search query for the model
    query = f"current job openings for '{search_term}' in '{location}' posted recently"
    
    prompt = f"""
You are an expert recruitment assistant. Use the Google Search tool to find current, active job openings for:
Role: {search_term}
Location: {location}

Search Google for job listings. Extract the following details for each job listing:
- title: The job title (e.g., "Python Backend Engineer").
- company: The hiring company (e.g., "TechCorp").
- location: The job location (e.g., "Austin, TX" or "Remote").
- job_url: The actual URL link to the job posting or application page.
- salary: Salary information if available (or empty string).
- description: A brief summary of the role, key responsibilities, and requirements.
- date_posted: Date/time posted (or empty string).
- job_type: Job type (e.g., "Full-time", "Contract", "Part-time", "Internship" or "Unknown").
- is_remote: Boolean (true if the job is remote, false otherwise).
- site: The source platform name (e.g., "LinkedIn", "Indeed", "Company Website").

Return the list of job listings STRICTLY as a JSON object with a single key "jobs":
{{
  "jobs": [
    {{
      "title": "...",
      "company": "...",
      "location": "...",
      "job_url": "...",
      "salary": "...",
      "description": "...",
      "date_posted": "...",
      "job_type": "...",
      "is_remote": true,
      "site": "..."
    }}
  ]
}}
Do NOT output any markdown code blocks (like ```json). Just the raw JSON object.
"""

    try:
        logger.info(f"Running Gemini Google Search Grounding for query: {query}")
        search_tool = types.Tool(google_search=types.GoogleSearch())
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[search_tool],
                response_mime_type="application/json"
            )
        )
        
        raw_text = response.text.strip()
        
        # Clean up any potential markdown formatting
        if raw_text.startswith("```"):
            raw_text = re.sub(r'^```[a-zA-Z]*\n', '', raw_text)
            raw_text = re.sub(r'\n```$', '', raw_text)
            raw_text = raw_text.strip()
            
        data = json.loads(raw_text)
        jobs_list = data.get("jobs", [])
        
        normalized = []
        for job in jobs_list[:results_wanted]:
            normalized.append({
                "id": str(uuid.uuid4()) if "id" not in job else job["id"],
                "site": str(job.get("site", "Gemini Search")),
                "job_url": str(job.get("job_url", "")),
                "title": str(job.get("title", "No Title")),
                "company": str(job.get("company", "Unknown Company")),
                "location": str(job.get("location", location)),
                "date_posted": str(job.get("date_posted", "")),
                "job_type": str(job.get("job_type", "")),
                "is_remote": bool(job.get("is_remote", False)),
                "salary": str(job.get("salary", "")),
                "description": str(job.get("description", ""))
            })
        logger.info(f"Gemini Google Search discovered {len(normalized)} jobs.")
        return enrich_job_descriptions(normalized)
        
    except Exception as e:
        logger.error(f"Error running Gemini Google Search: {str(e)}", exc_info=True)
        return []

def fetch_external_job_description(url: str) -> str:
    """
    Crawls the given URL directly to fetch and parse the page content,
    extracting the clean job description text. Filters out navigation, footer,
    and script tags.
    """
    if not url or not url.startswith("http"):
        return ""
    
    parsed_url = url.lower()
    # Avoid scraping LinkedIn and Indeed directly as they block standard requests
    if "linkedin.com" in parsed_url or "indeed.com" in parsed_url:
        return ""
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    }
    
    try:
        import requests
        from bs4 import BeautifulSoup
        
        logger.info(f"Enriching job description by crawling external URL: {url}")
        response = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch external URL {url}: Status code {response.status_code}")
            return ""
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove non-content elements
        for element in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "iframe"]):
            element.decompose()
            
        # Target common job description containers
        possible_containers = []
        for tag in ["div", "section", "article", "main"]:
            for item in soup.find_all(tag):
                element_id = (item.get("id") or "").lower()
                classes = " ".join(item.get("class") or []).lower()
                auto_id = (item.get("data-automation-id") or "").lower()
                
                if (
                    "jobdescription" in auto_id or
                    "description" in element_id or "description" in classes or
                    "job-details" in element_id or "job-details" in classes or
                    "job-posting" in element_id or "job-posting" in classes or
                    "posting-content" in element_id or "posting-content" in classes or
                    "career" in element_id or "career" in classes
                ):
                    possible_containers.append((item, len(item.get_text())))
                    
        # Filter containers having text length in valid range
        valid_containers = [c for c in possible_containers if 300 <= c[1] <= 15000]
        if valid_containers:
            # Pick largest matching container text
            best_container = max(valid_containers, key=lambda x: x[1])[0]
            text = best_container.get_text(separator="\n")
        else:
            # Fallback to body text or entire text
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n")
            else:
                text = soup.get_text(separator="\n")
                
        # Clean lines
        lines = [line.strip() for line in text.splitlines()]
        cleaned_lines = [line for line in lines if line]
        
        final_text = "\n".join(cleaned_lines)
        if len(final_text) > 8000:
            final_text = final_text[:8000] + "\n[Description truncated...]"
        return final_text
        
    except Exception as e:
        logger.error(f"Error fetching external job description from {url}: {str(e)}")
        return ""

def enrich_job_descriptions(jobs: List[Dict[str, Any]], max_workers: int = 5) -> List[Dict[str, Any]]:
    """
    Enriches job listings concurrently using a ThreadPoolExecutor to crawl
    external job urls for missing or extremely short description texts.
    """
    from concurrent.futures import ThreadPoolExecutor
    
    jobs_to_enrich = []
    for job in jobs:
        desc = job.get("description") or ""
        url = job.get("job_url") or ""
        # Check if description is short and url is valid
        if len(desc) < 350 and url.startswith("http"):
            parsed_url = url.lower()
            if "linkedin.com" not in parsed_url and "indeed.com" not in parsed_url:
                jobs_to_enrich.append(job)
                
    if not jobs_to_enrich:
        return jobs
        
    logger.info(f"Found {len(jobs_to_enrich)} jobs needing description enrichment. Enriching concurrently...")
    
    def worker(job_dict):
        url = job_dict["job_url"]
        fetched_desc = fetch_external_job_description(url)
        if fetched_desc and len(fetched_desc) > len(job_dict.get("description") or ""):
            job_dict["description"] = fetched_desc
            logger.info(f"Successfully enriched description for '{job_dict.get('title')}' from {url} (Length: {len(fetched_desc)})")
            
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(worker, jobs_to_enrich)
        
    return jobs

if __name__ == "__main__":
    # Quick standalone test
    results = run_job_search("Python Software Engineer", "Austin, TX", results_wanted=3, site_names=["linkedin"])
    print(f"Scraped {len(results)} jobs.")
    if results:
        print("First result title:", results[0]["title"])
        print("First result url:", results[0]["job_url"])
