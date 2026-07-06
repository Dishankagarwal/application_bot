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
    "resume_style": {
        "font_family": "sans-serif",
        "accent_color": "#1e3a8a",
        "layout": "single_column"
    },
    "scraped_jobs": {}  # Map of job_id -> job_dict
}

class SearchRequest(BaseModel):
    search_term: str
    location: str
    results_wanted: Optional[int] = 15
    site_names: Optional[List[str]] = ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "naukri", "bayt"]
    job_type: Optional[str] = None
    min_salary: Optional[int] = None
    max_salary: Optional[int] = None
    hours_old: Optional[int] = None

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
    """Receives a PDF resume, parses its text and visual style, and stores them in memory."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    try:
        # Create temp folder to save PDF
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        file_path = os.path.join(temp_dir, file.filename)
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
            
        # Parse PDF and styling parameters
        matcher = JobMatcher()
        resume_text, resume_style = matcher.extract_text_and_style(file_path)
        
        # Save to memory
        SESSION_DATA["resume_text"] = resume_text
        SESSION_DATA["resume_filename"] = file.filename
        SESSION_DATA["resume_style"] = resume_style
        
        # Clean up temp file
        try:
            os.remove(file_path)
        except Exception:
            pass
            
        return {
            "success": True,
            "filename": file.filename,
            "char_count": len(resume_text),
            "style": resume_style,
            "message": "Resume uploaded, visual style analyzed, and text parsed successfully."
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
        site_names=req.site_names,
        job_type=req.job_type,
        min_amount=req.min_salary,
        max_amount=req.max_salary,
        hours_old=req.hours_old
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

@app.get("/api/download-tailored-pdf")
def download_tailored_pdf(job_id: str):
    """Generates tailored resume details, renders it to HTML, compiles to PDF and returns the file download."""
    job = SESSION_DATA["scraped_jobs"].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID not found or search results expired.")
        
    resume_text = SESSION_DATA["resume_text"]
    if not resume_text:
        raise HTTPException(status_code=400, detail="Please upload a resume before tailoring.")
        
    try:
        # 1. Tailor the resume content
        tailor = ResumeTailor()
        tailor_result = tailor.tailor_resume(
            resume_text=resume_text,
            job_title=job["title"],
            job_desc=job["description"]
        )
        tailored_markdown = tailor_result.get("tailored_resume_markdown", "")
        
        # 2. Convert tailored markdown to HTML
        import markdown
        html_content = markdown.markdown(tailored_markdown)
        
        # 3. Apply style properties detected from the uploaded resume
        resume_style = SESSION_DATA.get("resume_style", {
            "font_family": "sans-serif",
            "accent_color": "#1e3a8a",
            "layout": "single_column"
        })
        
        font_family_css = "Helvetica, Arial, sans-serif"
        if resume_style.get("font_family") == "serif":
            font_family_css = "Times New Roman, Times, serif"
            
        accent_color = resume_style.get("accent_color", "#1e3a8a")
        
        # Generate inline CSS for xhtml2pdf
        css_style = f"""
        @page {{
            size: letter;
            margin: 1.5cm 1.5cm 1.5cm 1.5cm;
            @bottom-right {{
                content: "Page " counter(page);
                font-size: 8pt;
                color: #94a3b8;
            }}
        }}
        body {{
            font-family: {font_family_css};
            font-size: 10pt;
            line-height: 1.4;
            color: #1e293b;
        }}
        h1 {{
            font-size: 18pt;
            font-weight: bold;
            color: {accent_color};
            margin-bottom: 5px;
            text-align: center;
        }}
        h2 {{
            font-size: 12pt;
            font-weight: bold;
            color: {accent_color};
            border-bottom: 1px solid {accent_color};
            padding-bottom: 2px;
            margin-top: 15px;
            margin-bottom: 8px;
            text-transform: uppercase;
        }}
        h3 {{
            font-size: 10pt;
            font-weight: bold;
            color: #1e293b;
            margin-top: 8px;
            margin-bottom: 3px;
        }}
        p {{
            margin-top: 0px;
            margin-bottom: 5px;
        }}
        ul {{
            margin-top: 0px;
            margin-bottom: 5px;
            padding-left: 15px;
        }}
        li {{
            margin-bottom: 3px;
        }}
        .header-contact {{
            text-align: center;
            font-size: 9pt;
            color: #475569;
            margin-bottom: 15px;
        }}
        """
        
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
            {css_style}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
        
        # 4. Generate PDF bytes using xhtml2pdf
        from io import BytesIO
        from xhtml2pdf import pisa
        
        pdf_io = BytesIO()
        pisa_status = pisa.CreatePDF(full_html, dest=pdf_io)
        
        if pisa_status.err:
            raise Exception(f"Failed to generate PDF: {pisa_status.err}")
            
        pdf_io.seek(0)
        
        # Format file name
        safe_company = job["company"].replace(" ", "_").strip()
        safe_title = job["title"].replace(" ", "_").strip()
        # Remove characters that aren't letters, numbers, underscores, or dashes
        import re
        safe_company = re.sub(r'[^\w\-]', '', safe_company)
        safe_title = re.sub(r'[^\w\-]', '', safe_title)
        filename = f"Tailored_Resume_{safe_company}_{safe_title}.pdf"
        
        # Return streaming response
        from fastapi.responses import StreamingResponse
        import urllib.parse
        headers = {
            'Content-Disposition': f'attachment; filename="{urllib.parse.quote(filename)}"',
            'Access-Control-Expose-Headers': 'Content-Disposition'
        }
        return StreamingResponse(pdf_io, media_type="application/pdf", headers=headers)
        
    except Exception as e:
        logger.error(f"Error generating tailored PDF resume: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to build PDF: {str(e)}")

