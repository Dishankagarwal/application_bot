import os
import uuid
import logging
from typing import List, Optional, Dict
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from scraper import run_job_search, run_gemini_google_search, deduplicate_jobs
from matcher import JobMatcher
from tailor import ResumeTailor
from interview_prep import InterviewPrepGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def filter_scraped_jobs(
    jobs: List[Dict],
    job_type: Optional[str],
    min_salary: Optional[int],
    max_salary: Optional[int],
    location: Optional[str]
) -> List[Dict]:
    """
    Applies post-scraping filtering for Location, Job Type, and Salary range.
    """
    filtered = []
    
    is_remote_query = False
    if location and "remote" in location.lower():
        is_remote_query = True
        
    for job in jobs:
        # 1. Filter by location if 'Remote' is requested
        if is_remote_query:
            is_remote_job = job.get("is_remote", False)
            job_loc = job.get("location", "").lower()
            if not is_remote_job and "remote" not in job_loc:
                logger.info(f"Filtering out onsite job: '{job.get('title')}' at '{job.get('company')}' (Location: {job.get('location')})")
                continue
                
        # 2. Filter by Job Type (Full time / Contract)
        if job_type and job_type != "any":
            jtype = job.get("job_type", "").lower()
            if jtype:
                if job_type == "fulltime" and "full" not in jtype and "permanent" not in jtype:
                    logger.info(f"Filtering out non-fulltime job: '{job.get('title')}' (Type: {job.get('job_type')})")
                    continue
                if job_type == "contract" and "contract" not in jtype and "freelance" not in jtype:
                    logger.info(f"Filtering out non-contract job: '{job.get('title')}' (Type: {job.get('job_type')})")
                    continue
            else:
                title_lower = job.get("title", "").lower()
                desc_lower = job.get("description", "").lower()
                if job_type == "fulltime":
                    if ("contract" in title_lower or "intern" in title_lower) and "full time" not in desc_lower and "full-time" not in desc_lower:
                        logger.info(f"Filtering out suspected contract/intern job: '{job.get('title')}'")
                        continue
                elif job_type == "contract":
                    if "full-time" in title_lower or "permanent" in title_lower:
                        logger.info(f"Filtering out suspected full-time job: '{job.get('title')}'")
                        continue

        # 3. Filter by Salary Range (if salary is specified in the job posting)
        if min_salary or max_salary:
            salary_str = job.get("salary", "")
            if salary_str:
                import re
                numbers = [int(s.replace(',', '')) for s in re.findall(r'\b\d{1,3}(?:,\d{3})+\b', salary_str)]
                if not numbers:
                    numbers = [int(s) for s in re.findall(r'\b\d{5,6}\b', salary_str)]
                    
                if numbers:
                    is_hourly = "hour" in salary_str.lower() or max(numbers) < 1000
                    normalized_numbers = []
                    for num in numbers:
                        if is_hourly:
                            normalized_numbers.append(num * 2000)
                        else:
                            normalized_numbers.append(num)
                            
                    job_min = min(normalized_numbers)
                    job_max = max(normalized_numbers)
                    
                    if min_salary and job_max < min_salary:
                        logger.info(f"Filtering out job with low salary: '{job.get('title')}' (Salary: {salary_str})")
                        continue
                    if max_salary and job_min > max_salary:
                        logger.info(f"Filtering out job with high salary: '{job.get('title')}' (Salary: {salary_str})")
                        continue
                        
        filtered.append(job)
    return filtered


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
    site_names: Optional[List[str]] = ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "naukri", "bayt", "gemini_search"]
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
    raw_jobs = []
    for site in req.site_names:
        try:
            if site == "gemini_search":
                site_jobs = run_gemini_google_search(
                    search_term=req.search_term,
                    location=req.location,
                    results_wanted=req.results_wanted
                )
            else:
                site_jobs = run_job_search(
                    search_term=req.search_term,
                    location=req.location,
                    results_wanted=req.results_wanted,
                    site_names=[site],
                    job_type=req.job_type,
                    min_amount=req.min_salary,
                    max_amount=req.max_salary,
                    hours_old=req.hours_old
                )
            if site_jobs:
                raw_jobs.extend(site_jobs)
        except Exception as err:
            logger.error(f"Error scraping {site} in REST: {str(err)}")
            
    # Apply post-scraping smart filters
    raw_jobs = filter_scraped_jobs(
        jobs=raw_jobs,
        job_type=req.job_type,
        min_salary=req.min_salary,
        max_salary=req.max_salary,
        location=req.location
    )
    
    # Deduplicate cross-platform listings
    raw_jobs = deduplicate_jobs(raw_jobs)
    
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
                
            # Log description lengths for visibility
            for j in jobs_to_analyze:
                logger.info(f"Job title: '{j.get('title')}', Company: '{j.get('company')}', Description length: {len(j.get('description') or '')}")

            # Perform batch analysis (1 API call instead of 15!)
            batch_results = matcher.analyze_matches_batch(resume_text, jobs_to_analyze)
            
            for job in jobs_to_analyze:
                job_id = job["id"]
                analysis = batch_results.get(job_id)
                if analysis:
                    # Compute ATS keyword hit rate locally
                    ats_data = matcher.compute_ats_keyword_hit_rate(
                        resume_text,
                        analysis.get("matched_keywords", []),
                        analysis.get("missing_keywords", [])
                    )
                    job.update({
                        "match_score": analysis.get("match_score", 50),
                        "skills_score": analysis.get("skills_score", 50),
                        "experience_score": analysis.get("experience_score", 50),
                        "education_score": analysis.get("education_score", 50),
                        "overall_score": analysis.get("overall_score", 50),
                        "ats_keyword_hit_rate": ats_data.get("ats_keyword_hit_rate", 0),
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
                        "skills_score": 0,
                        "experience_score": 0,
                        "education_score": 0,
                        "overall_score": 0,
                        "ats_keyword_hit_rate": 0,
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


@app.websocket("/api/search-jobs-ws")
async def search_jobs_websocket(websocket: WebSocket):
    """
    WebSocket endpoint that scrapes job listings sequentially across multiple boards
    and streams progress updates and final Gemini-ranked matches back to the client.
    """
    await websocket.accept()
    try:
        # 1. Receive search criteria from frontend
        req_data = await websocket.receive_json()
        search_term = req_data.get("search_term", "")
        location = req_data.get("location", "")
        results_wanted = req_data.get("results_wanted", 15)
        site_names = req_data.get("site_names", ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "naukri", "bayt", "gemini_search"])
        job_type = req_data.get("job_type")
        min_salary = req_data.get("min_salary")
        max_salary = req_data.get("max_salary")
        hours_old = req_data.get("hours_old")
        
        if not search_term:
            await websocket.send_json({"type": "error", "message": "Search keyword is required."})
            await websocket.close()
            return

        raw_jobs = []
        # Loop through site_names sequentially
        for site in site_names:
            site_display_name = site.replace("_", " ").title()
            await websocket.send_json({
                "type": "progress",
                "message": f"Searching {site_display_name}...",
                "current_site": site,
                "jobs_found": len(raw_jobs)
            })
            
            try:
                # Scrape single site in a background thread to prevent blocking
                if site == "gemini_search":
                    site_jobs = await asyncio.to_thread(
                        run_gemini_google_search,
                        search_term=search_term,
                        location=location,
                        results_wanted=results_wanted
                    )
                else:
                    site_jobs = await asyncio.to_thread(
                        run_job_search,
                        search_term=search_term,
                        location=location,
                        results_wanted=results_wanted,
                        site_names=[site],
                        job_type=job_type,
                        min_amount=min_salary,
                        max_amount=max_salary,
                        hours_old=hours_old
                    )
                if site_jobs:
                    raw_jobs.extend(site_jobs)
            except Exception as scrape_err:
                logger.error(f"Error scraping {site}: {str(scrape_err)}")
                continue

        # Apply post-scraping smart filters
        raw_jobs = filter_scraped_jobs(
            jobs=raw_jobs,
            job_type=job_type,
            min_salary=min_salary,
            max_salary=max_salary,
            location=location
        )

        # Deduplicate cross-platform listings
        raw_jobs = deduplicate_jobs(raw_jobs)

        if not raw_jobs:
            await websocket.send_json({
                "type": "results",
                "jobs": []
            })
            await websocket.close()
            return

        await websocket.send_json({
            "type": "progress",
            "message": f"Scraped {len(raw_jobs)} filtered jobs. Analyzing relevance with Gemini...",
            "current_site": "gemini",
            "jobs_found": len(raw_jobs)
        })

        # Process and compute relevance scores
        resume_text = SESSION_DATA["resume_text"]
        processed_jobs = []
        
        # Cap the jobs to analyze to avoid hitting rate/time limits
        jobs_to_analyze_raw = raw_jobs[:25]
        
        # Reset memory stored jobs
        SESSION_DATA["scraped_jobs"] = {}
        
        if resume_text:
            try:
                matcher = JobMatcher()
                jobs_to_analyze = []
                for job in jobs_to_analyze_raw:
                    job_id = str(uuid.uuid4())
                    job["id"] = job_id
                    SESSION_DATA["scraped_jobs"][job_id] = job
                    jobs_to_analyze.append(job)
                
                # Log description lengths for visibility
                for j in jobs_to_analyze:
                    logger.info(f"Job title: '{j.get('title')}', Company: '{j.get('company')}', Description length: {len(j.get('description') or '')}")

                # Perform batch analysis in background thread
                batch_results = await asyncio.to_thread(
                    matcher.analyze_matches_batch,
                    resume_text,
                    jobs_to_analyze
                )
                
                for job in jobs_to_analyze:
                    job_id = job["id"]
                    analysis = batch_results.get(job_id)
                    if analysis:
                        # Compute ATS keyword hit rate locally
                        ats_data = matcher.compute_ats_keyword_hit_rate(
                            resume_text,
                            analysis.get("matched_keywords", []),
                            analysis.get("missing_keywords", [])
                        )
                        job.update({
                            "match_score": analysis.get("match_score", 50),
                            "skills_score": analysis.get("skills_score", 50),
                            "experience_score": analysis.get("experience_score", 50),
                            "education_score": analysis.get("education_score", 50),
                            "overall_score": analysis.get("overall_score", 50),
                            "ats_keyword_hit_rate": ats_data.get("ats_keyword_hit_rate", 0),
                            "match_summary": analysis.get("summary", ""),
                            "matched_keywords": analysis.get("matched_keywords", []),
                            "missing_keywords": analysis.get("missing_keywords", []),
                            "strengths": analysis.get("strengths", []),
                            "weaknesses": analysis.get("weaknesses", [])
                        })
                    else:
                        job.update({
                            "match_score": 0,
                            "skills_score": 0,
                            "experience_score": 0,
                            "education_score": 0,
                            "overall_score": 0,
                            "ats_keyword_hit_rate": 0,
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
                logger.error(f"Error during batch match analysis in WS: {str(e)}")
                for job in jobs_to_analyze_raw:
                    job_id = str(uuid.uuid4())
                    job["id"] = job_id
                    job.update({
                        "match_score": 0,
                        "match_summary": "Skipped score computation due to server error."
                    })
                    SESSION_DATA["scraped_jobs"][job_id] = job
                    processed_jobs.append(job)
        else:
            # No resume uploaded
            for job in jobs_to_analyze_raw:
                job_id = str(uuid.uuid4())
                job["id"] = job_id
                job.update({
                    "match_score": 0,
                    "match_summary": "Upload a resume first to see a match score."
                })
                SESSION_DATA["scraped_jobs"][job_id] = job
                processed_jobs.append(job)
                
        # Send final results to frontend
        await websocket.send_json({
            "type": "results",
            "jobs": processed_jobs
        })
        
    except WebSocketDisconnect:
        logger.info("Search jobs WebSocket disconnected.")
    except Exception as e:
        logger.error(f"Unhandled WebSocket error: {str(e)}")
        try:
            await websocket.send_json({"type": "error", "message": f"Server error: {str(e)}"})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


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
        logger.error(f"Error in tailor endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-cover-letter")
def generate_cover_letter(req: TailorRequest):
    """Generates a tailored cover letter for a specific stored job."""
    job_id = req.job_id
    job = SESSION_DATA["scraped_jobs"].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID not found or search results expired.")
        
    resume_text = SESSION_DATA["resume_text"]
    if not resume_text:
        raise HTTPException(status_code=400, detail="Please upload a resume before generating a cover letter.")
        
    try:
        tailor = ResumeTailor()
        return tailor.generate_cover_letter(
            resume_text=resume_text,
            job_title=job["title"],
            job_desc=job["description"],
            company=job["company"]
        )
    except Exception as e:
        logger.error(f"Error in cover letter endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/interview-prep")
def generate_interview_prep(req: TailorRequest):
    """Generates interview questions and approaches for a specific stored job."""
    job_id = req.job_id
    job = SESSION_DATA["scraped_jobs"].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID not found or search results expired.")
        
    resume_text = SESSION_DATA["resume_text"]
    if not resume_text:
        raise HTTPException(status_code=400, detail="Please upload a resume before generating interview prep.")
        
    try:
        prep = InterviewPrepGenerator()
        weaknesses = job.get("weaknesses", [])
        return prep.generate_interview_questions(
            resume_text=resume_text,
            job_title=job["title"],
            job_desc=job["description"],
            weaknesses=weaknesses
        )
    except Exception as e:
        logger.error(f"Error in interview prep endpoint: {str(e)}")
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
        orig_filename = SESSION_DATA.get("resume_filename", "")
        prefix = "Tailored_Resume"
        if orig_filename:
            if orig_filename.lower().endswith(".pdf"):
                prefix = orig_filename[:-4]
            else:
                prefix = orig_filename
        
        # Clean up components to consist only of letters, numbers, underscores, and dashes
        import re
        prefix = prefix.replace(" ", "_").strip()
        prefix = re.sub(r'[^\w\-]', '', prefix)
        
        safe_company = job["company"].replace(" ", "_").strip()
        safe_title = job["title"].replace(" ", "_").strip()
        safe_company = re.sub(r'[^\w\-]', '', safe_company)
        safe_title = re.sub(r'[^\w\-]', '', safe_title)
        
        filename = f"{prefix}_Tailored_{safe_company}_{safe_title}.pdf"
        
        # Return streaming response
        from fastapi.responses import StreamingResponse
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Access-Control-Expose-Headers': 'Content-Disposition'
        }
        return StreamingResponse(pdf_io, media_type="application/octet-stream", headers=headers)
        
    except Exception as e:
        logger.error(f"Error generating tailored PDF resume: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to build PDF: {str(e)}")

