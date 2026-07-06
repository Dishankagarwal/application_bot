import math
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
    site_names: List[str] = None
) -> List[Dict[str, Any]]:
    """
    Scrapes jobs from multiple job boards using python-jobspy.
    Normalizes the output DataFrame to a JSON-serializable list of dictionaries.
    """
    if not site_names:
        site_names = ["linkedin", "indeed", "zip_recruiter", "glassdoor"]
    
    logger.info(f"Searching for '{search_term}' in '{location}' across {site_names}...")
    
    try:
        # python-jobspy returns a pandas DataFrame
        jobs_df = scrape_jobs(
            site_name=site_names,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            country_indeed='USA'  # Default country context
        )
        
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

if __name__ == "__main__":
    # Quick standalone test
    results = run_job_search("Python Software Engineer", "Austin, TX", results_wanted=3, site_names=["linkedin"])
    print(f"Scraped {len(results)} jobs.")
    if results:
        print("First result title:", results[0]["title"])
        print("First result url:", results[0]["job_url"])
