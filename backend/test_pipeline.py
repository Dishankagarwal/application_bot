import os
import sys
from dotenv import load_dotenv

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import run_job_search
from matcher import JobMatcher
from tailor import ResumeTailor

def test_pipeline():
    print("="*60)
    print("Running Job Application Bot Backend Pipeline Verification")
    print("="*60)
    
    # Load environment variables
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[FAIL] GEMINI_API_KEY environment variable is missing. Check backend/.env")
        return False
        
    print("[PASS] GEMINI_API_KEY detected.")
    
    # 1. Test Scraper
    print("\n--- Testing Scraper ---")
    try:
        jobs = run_job_search("Python Developer", "Dallas, TX", results_wanted=2, site_names=["linkedin"])
        print(f"[PASS] Successfully scraped {len(jobs)} jobs from LinkedIn.")
        if jobs:
            print(f"       Example job: '{jobs[0]['title']}' at '{jobs[0]['company']}'")
            print(f"       Job URL: {jobs[0]['job_url']}")
        else:
            print("       No jobs found, but scraping completed without error.")
    except Exception as e:
        print(f"[FAIL] Scraper failed: {e}")
        return False
        
    # 2. Test Matcher
    print("\n--- Testing LLM Matcher ---")
    mock_resume = """
    John Doe
    Python Developer with 3 years of experience.
    Skills: Python, FastAPI, Django, PostgreSQL, Git.
    Experience: Web Developer at TechCorp. Built REST APIs using FastAPI and managed PostgreSQL databases.
    """
    mock_job_title = "Senior Python/FastAPI Backend Engineer"
    mock_job_desc = """
    We are looking for a Senior Python Developer.
    Requirements:
    - 5+ years of experience with Python
    - Strong skills in FastAPI or Django
    - Experience with Docker and AWS
    - Knowledge of PostgreSQL databases
    """
    
    try:
        matcher = JobMatcher()
        analysis = matcher.analyze_match(mock_resume, mock_job_title, mock_job_desc)
        print("[PASS] LLM Matcher returned analysis successfully.")
        print(f"       Match Score: {analysis.get('match_score')}%")
        print(f"       Summary: {analysis.get('summary')}")
        print(f"       Matched Skills: {analysis.get('matched_keywords')}")
        print(f"       Missing Skills: {analysis.get('missing_keywords')}")
    except Exception as e:
        print(f"[FAIL] Matcher failed: {e}")
        return False
        
    # 3. Test Resume Tailor
    print("\n--- Testing Resume Tailor ---")
    try:
        tailor = ResumeTailor()
        tailored = tailor.tailor_resume(mock_resume, mock_job_title, mock_job_desc)
        print("[PASS] Resume Tailor returned tailored markdown successfully.")
        print(f"       Tailored Resume Sample (First 150 chars):\n{tailored.get('tailored_resume_markdown')[:150]}...")
        print(f"       Changes Made:")
        for idx, change in enumerate(tailored.get('changes_made', [])[:2]):
            print(f"         {idx+1}. [{change.get('section')}] {change.get('change')}")
    except Exception as e:
        print(f"[FAIL] Resume Tailor failed: {e}")
        return False
        
    print("\n" + "="*60)
    print("[SUCCESS] All backend pipeline components verified successfully.")
    print("="*60)
    return True

if __name__ == "__main__":
    test_pipeline()
