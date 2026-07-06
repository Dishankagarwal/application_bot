import logging
import pandas as pd
from typing import List, Dict, Any
from jobspy import scrape_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        
        # Replace NaN values with None so it converts to clean JSON null values
        jobs_df = jobs_df.where(pd.notnull(jobs_df), None)
        
        normalized_jobs = []
        for _, row in jobs_df.iterrows():
            # Get job description, handling potential missing columns
            desc = row.get("description") or ""
            
            # Format salary if present
            salary = ""
            min_sal = row.get("min_amount")
            max_sal = row.get("max_amount")
            curr = row.get("currency") or "$"
            interval = row.get("interval") or "yearly"
            
            if min_sal and max_sal:
                salary = f"{curr}{min_sal:,} - {curr}{max_sal:,} per {interval}"
            elif min_sal:
                salary = f"{curr}{min_sal:,}+ per {interval}"
            elif max_sal:
                salary = f"Up to {curr}{max_sal:,} per {interval}"
                
            job_item = {
                "id": str(row.get("id") or ""),
                "site": str(row.get("site") or "unknown"),
                "job_url": str(row.get("job_url") or row.get("job_url_direct") or ""),
                "title": str(row.get("title") or "No Title"),
                "company": str(row.get("company") or "Unknown Company"),
                "location": str(row.get("location") or "Unknown Location"),
                "date_posted": str(row.get("date_posted") or ""),
                "job_type": str(row.get("job_type") or ""),
                "is_remote": bool(row.get("is_remote")) if row.get("is_remote") is not None else False,
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
