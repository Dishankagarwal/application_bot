import os
import uuid
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from scraper import run_job_search
from matcher import JobMatcher
from tailor import ResumeTailor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Job Application Bot Backend")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables for session/storage (in-memory)
SESSION_DATA = {
    "resume_text": "",
    "resume_filename": "",
    "scraped_jobs": {}  # Map of job_id -> job_dict
}

class SearchRequest(BaseModel):
    search_term: str
    location: str
    results_wanted: Optional[int] = 15
    site_names: Optional[List[str]] = ["linkedin", "indeed", "zip_recruiter", "glassdoor"]

class TailorRequest(BaseModel):
    job_id: str

@app.get("/api/status")
def health_check():
    """Health check endpoint showing active session state."""
    return {
        "status": "healthy",
        "has_resume": bool(SESSION_DATA["resume_text"]),
        "resume_filename": SESSION_DATA["resume_filename"],
        "jobs_stored_count": len(SESSION_DATA["scraped_jobs"])
    }

@app.post("/api/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    """Receives a PDF resume, parses its text, and stores it in memory."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    try:
        # Create temp folder to save PDF
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        file_path = os.path.join(temp_dir, file.filename)
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
            
        # Parse PDF
        matcher = JobMatcher()
        resume_text = matcher.extract_text_from_pdf(file_path)
        
        # Save to memory
        SESSION_DATA["resume_text"] = resume_text
        SESSION_DATA["resume_filename"] = file.filename
        
        # Clean up temp file
        try:
            os.remove(file_path)
        except Exception:
            pass
            
        return {
            "success": True,
            "filename": file.filename,
            "char_count": len(resume_text),
            "message": "Resume uploaded and parsed successfully."
        }
    except Exception as e:
        logger.error(f"Error handling resume upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process resume: {str(e)}")

@app.post("/api/search-jobs")
def search_jobs(req: SearchRequest):
    """
    Scrapes jobs matching search query, and computes relevance scores
    against the uploaded resume (if available).
    """
    # 1. Scrape jobs
    raw_jobs = run_job_search(
        search_term=req.search_term,
        location=req.location,
        results_wanted=req.results_wanted,
        site_names=req.site_names
    )
    
    if not raw_jobs:
        return {"success": True, "jobs": []}
        
    # 2. Check if a resume is present to run matcher
    resume_text = SESSION_DATA["resume_text"]
    processed_jobs = []
    
    # Store jobs in memory using generated unique IDs
    SESSION_DATA["scraped_jobs"] = {}
    
    if resume_text:
        try:
            matcher = JobMatcher()
            # Prepare jobs with unique IDs first
            jobs_to_analyze = []
            for job in raw_jobs:
                job_id = str(uuid.uuid4())
                job["id"] = job_id
                SESSION_DATA["scraped_jobs"][job_id] = job
                jobs_to_analyze.append(job)
                
            # Perform batch analysis (1 API call instead of 15!)
            batch_results = matcher.analyze_matches_batch(resume_text, jobs_to_analyze)
            
            for job in jobs_to_analyze:
                job_id = job["id"]
                analysis = batch_results.get(job_id)
                if analysis:
                    job.update({
                        "match_score": analysis.get("match_score", 50),
                        "match_summary": analysis.get("summary", ""),
                        "matched_keywords": analysis.get("matched_keywords", []),
                        "missing_keywords": analysis.get("missing_keywords", []),
                        "strengths": analysis.get("strengths", []),
                        "weaknesses": analysis.get("weaknesses", [])
                    })
                else:
                    # Fallback defaults if Gemini did not return a match for this job ID
                    job.update({
                        "match_score": 0,
                        "match_summary": "Skipped score computation due to API limit or parsing error.",
                        "matched_keywords": [],
                        "missing_keywords": [],
                        "strengths": [],
                        "weaknesses": ["No response from analysis server"]
                    })
                processed_jobs.append(job)
                
            # Sort by match score descending
            processed_jobs.sort(key=lambda x: x.get("match_score", 0), reverse=True)
            
        except Exception as e:
            logger.error(f"Error during match analysis: {str(e)}")
            # Fallback to standard jobs list without score
            for job in raw_jobs:
                job_id = str(uuid.uuid4())
                job["id"] = job_id
                job.update({
                    "match_score": 0,
                    "match_summary": "Skipped score computation due to server error."
                })
                SESSION_DATA["scraped_jobs"][job_id] = job
                processed_jobs.append(job)
    else:
        # No resume uploaded, just return jobs with score=0
        for job in raw_jobs:
            job_id = str(uuid.uuid4())
            job["id"] = job_id
            job.update({
                "match_score": 0,
                "match_summary": "Upload a resume first to see a match score."
            })
            SESSION_DATA["scraped_jobs"][job_id] = job
            processed_jobs.append(job)
            
    return {"success": True, "jobs": processed_jobs}

@app.post("/api/tailor-resume")
def tailor_resume(req: TailorRequest):
    """Generates tailored resume details for a specific stored job."""
    job_id = req.job_id
    job = SESSION_DATA["scraped_jobs"].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID not found or search results expired.")
        
    resume_text = SESSION_DATA["resume_text"]
    if not resume_text:
        raise HTTPException(status_code=400, detail="Please upload a resume before tailoring.")
        
    try:
        tailor = ResumeTailor()
        tailor_result = tailor.tailor_resume(
            resume_text=resume_text,
            job_title=job["title"],
            job_desc=job["description"]
        )
        return {
            "success": True,
            "job_title": job["title"],
            "company": job["company"],
            "tailored_resume_markdown": tailor_result.get("tailored_resume_markdown", ""),
            "changes_made": tailor_result.get("changes_made", [])
        }
    except Exception as e:
        logger.error(f"Error in tailor endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
