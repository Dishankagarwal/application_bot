import math
import logging
import pandas as pd
from typing import List, Dict, Any
from jobspy import scrape_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

        # python-jobspy returns a pandas DataFrame
        jobs_df = scrape_jobs(**scrape_kwargs)
        
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
            
        return normalized_jobs

    except Exception as e:
        logger.error(f"Error during job scraping: {str(e)}", exc_info=True)
        return []

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
        return normalized
        
    except Exception as e:
        logger.error(f"Error running Gemini Google Search: {str(e)}", exc_info=True)
        return []

if __name__ == "__main__":
    # Quick standalone test
    results = run_job_search("Python Software Engineer", "Austin, TX", results_wanted=3, site_names=["linkedin"])
    print(f"Scraped {len(results)} jobs.")
    if results:
        print("First result title:", results[0]["title"])
        print("First result url:", results[0]["job_url"])
