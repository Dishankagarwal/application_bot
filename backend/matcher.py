import os
import re
import json
import logging
from typing import Dict, Any, List
from pypdf import PdfReader
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class JobMatcher:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash", "gemini-2.0-flash"]

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extracts plain text from a PDF file using pypdf."""
        logger.info(f"Extracting text from PDF: {pdf_path}")
        try:
            reader = PdfReader(pdf_path)
            text_parts = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            full_text = "\n".join(text_parts).strip()
            if not full_text:
                raise ValueError("Could not extract any text from the PDF. It might be scanned or empty.")
            return full_text
        except Exception as e:
            logger.error(f"Failed to parse PDF resume: {str(e)}")
            raise

    def extract_text_and_style(self, pdf_path: str) -> tuple[str, dict]:
        """
        Extracts plain text and basic visual styling options from the PDF.
        Returns (resume_text, style_dict).
        """
        logger.info(f"Extracting text and styling from PDF: {pdf_path}")
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
        except ImportError:
            logger.warning("PyMuPDF (fitz) is not installed. Falling back to pypdf.")
            text = self.extract_text_from_pdf(pdf_path)
            return text, {
                "font_family": "sans-serif",
                "accent_color": "#1e3a8a",
                "layout": "single_column"
            }

        try:
            text_parts = []
            font_counts = {}
            color_counts = {}
            left_blocks_count = 0
            right_blocks_count = 0
            
            for page in doc:
                text_parts.append(page.get_text())
                page_width = page.rect.width
                page_dict = page.get_text("dict")
                
                for block in page_dict.get("blocks", []):
                    bbox = block.get("bbox")  # (x0, y0, x1, y1)
                    if bbox:
                        x0, y0, x1, y1 = bbox
                        if x1 <= page_width * 0.55:
                            left_blocks_count += 1
                        elif x0 >= page_width * 0.45:
                            right_blocks_count += 1
                            
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            font = span.get("font", "").lower()
                            color = span.get("color", 0)
                            text = span.get("text", "").strip()
                            
                            if font:
                                font_counts[font] = font_counts.get(font, 0) + len(text)
                            if color and text:
                                color_counts[color] = color_counts.get(color, 0) + len(text)

            full_text = "\n".join(text_parts).strip()
            
            # Detect font family
            font_family = "sans-serif"
            if font_counts:
                dominant_font = max(font_counts, key=font_counts.get)
                serif_keywords = ["times", "georgia", "garamond", "cambria", "serif", "roman", "minion", "baskerville", "palatino"]
                if any(keyword in dominant_font for keyword in serif_keywords):
                    font_family = "serif"
            
            # Detect accent color
            accent_color = "#1e3a8a" # Default slate/navy blue
            valid_colors = {}
            for color, count in color_counts.items():
                r = (color >> 16) & 255
                g = (color >> 8) & 255
                b = color & 255
                
                # Exclude black/very dark gray (all < 50) and white/very light gray (all > 235)
                if not (r < 50 and g < 50 and b < 50) and not (r > 235 and g > 235 and b > 235):
                    # Also make sure it's not a generic light gray (e.g. difference between channels is small)
                    if max(r, g, b) - min(r, g, b) > 20:
                        valid_colors[color] = count
                        
            if valid_colors:
                dominant_color_int = max(valid_colors, key=valid_colors.get)
                dr = (dominant_color_int >> 16) & 255
                dg = (dominant_color_int >> 8) & 255
                db = dominant_color_int & 255
                accent_color = f"#{dr:02x}{dg:02x}{db:02x}"
                
            # Detect layout
            layout = "single_column"
            if left_blocks_count > 10 and right_blocks_count > 10:
                layout = "two_column"
                
            style_dict = {
                "font_family": font_family,
                "accent_color": accent_color,
                "layout": layout
            }
            logger.info(f"Detected resume style: {style_dict}")
            return full_text, style_dict
            
        except Exception as e:
            logger.error(f"Error analyzing PDF style: {str(e)}")
            # Return text extraction only with default styling
            text = "\n".join([page.get_text() for page in doc]).strip()
            return text, {
                "font_family": "sans-serif",
                "accent_color": "#1e3a8a",
                "layout": "single_column"
            }

    def analyze_match(self, resume_text: str, job_title: str, job_desc: str) -> Dict[str, Any]:
        """
        Queries Gemini to analyze matching percentage, strengths, weaknesses,
        and missing keywords between the resume and the job description.
        Returns a structured JSON response.
        """
        logger.info(f"Analyzing match for target role: '{job_title}'")
        
        prompt = f"""
You are an expert recruiter and resume reviewer. Compare the Candidate Resume text with the Job Details below.

=== Job Details ===
Title: {job_title}
Description:
{job_desc}

=== Candidate Resume ===
{resume_text}

=== Output Instructions ===
Compare the candidate's skills, experience, and background against the job description.
Compute:
1. `match_score`: An integer from 0 to 100 representing how well the candidate fits the job. If the job description is extremely short, missing, or generic, evaluate based on the job title and general resume relevance, and assign a neutral score (e.g., 50-65) rather than penalizing the candidate heavily.
2. `summary`: A concise 2-sentence summary explaining why they are or are not a good fit. If description details are sparse, explicitly state: "Limited description details available for detailed mapping."
3. `matched_keywords`: Key technical skills/keywords that exist in both the job description (or title) and the resume.
4. `missing_keywords`: Important keywords/skills listed in the job description that are missing from the resume. Leave empty if description is sparse.
5. `strengths`: Bullet points listing the candidate's main strengths matching the job.
6. `weaknesses`: Bullet points listing gaps or weaknesses relative to the job requirements.

Return the result STRICTLY as a JSON object with this exact keys:
{{
  "match_score": 85,
  "summary": "Candidate has strong backend experience matching FastAPI and Python requirements. However, they lack the Docker containerization skills.",
  "matched_keywords": ["Python", "FastAPI", "SQL"],
  "missing_keywords": ["Docker", "Kubernetes"],
  "strengths": ["3+ years of python development", "Experience building REST APIs"],
  "weaknesses": ["No containerization experience listed", "No CI/CD pipeline experience"]
}}
Do NOT output any markdown blocks like ```json or any other text before/after the JSON. Just the raw JSON object.
"""
        last_error = None
        for model in self.models:
            logger.info(f"Attempting match analysis with model {model}...")
            try:
                # We can use the Structured JSON feature in Gemini GenAI SDK:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                raw_text = response.text.strip()
                # Parse response as JSON
                analysis_result = json.loads(raw_text)
                
                # Ensure essential keys exist
                defaults = {
                    "match_score": 50,
                    "summary": "Match computed successfully.",
                    "matched_keywords": [],
                    "missing_keywords": [],
                    "strengths": [],
                    "weaknesses": []
                }
                for k, v in defaults.items():
                    if k not in analysis_result:
                        analysis_result[k] = v
                        
                return analysis_result

            except Exception as e:
                logger.warning(f"Gemini match analysis failed with model {model}: {str(e)}")
                last_error = e

        logger.error(f"All Gemini models failed for match analysis. Last error: {str(last_error)}", exc_info=True)
        return {
            "match_score": 0,
            "summary": f"Failed to perform matching: {str(last_error)}",
            "matched_keywords": [],
            "missing_keywords": [],
            "strengths": [],
            "weaknesses": ["Error in Gemini analysis"]
        }

    def analyze_matches_batch(self, resume_text: str, jobs: list) -> Dict[str, Dict[str, Any]]:
        """
        Analyzes a batch of jobs against the resume in a single Gemini call.
        Returns a dict mapping job_id -> match analysis dict with multi-dimensional scores.
        """
        logger.info(f"Analyzing {len(jobs)} jobs in batch...")
        if not jobs:
            return {}
            
        # Format the jobs for the prompt, truncating descriptions to keep context concise
        formatted_jobs = []
        for job in jobs:
            desc = job.get("description") or ""
            truncated_desc = desc[:2500] + ("..." if len(desc) > 2500 else "")
            formatted_jobs.append({
                "id": job["id"],
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "description": truncated_desc
            })
            
        prompt = f"""
You are an expert recruiter and resume reviewer. Compare the Candidate Resume text against the list of Job Openings.

=== Candidate Resume ===
{resume_text}

=== Job Openings ===
{json.dumps(formatted_jobs, indent=2)}

=== Output Instructions ===
Compare the candidate's resume against each job opening. For each job, compute:
1. `skills_score`: An integer from 0 to 100 representing how well the candidate's technical skills match the job requirements.
2. `experience_score`: An integer from 0 to 100 representing how well the candidate's years of experience and seniority level align with the job.
3. `education_score`: An integer from 0 to 100 representing how well the candidate's education matches the job requirements.
4. `overall_score`: A weighted composite integer from 0 to 100 calculated as: Skills 50% + Experience 30% + Education 20%. If the job description is extremely short, missing, or generic, evaluate based on the job title and general resume relevance, and assign a neutral score (e.g., 50-65) rather than penalizing the candidate heavily.
5. `summary`: A concise 2-sentence summary explaining why they are or are not a good fit. If description details are sparse, explicitly state: "Limited description details available for detailed mapping."
6. `matched_keywords`: Key technical skills/keywords that exist in both the job description (or title) and the resume.
7. `missing_keywords`: Important keywords/skills listed in the job description that are missing from the resume. Leave empty if description is sparse.
8. `strengths`: Bullet points listing the candidate's main strengths matching the job.
9. `weaknesses`: Bullet points listing gaps or weaknesses relative to the job requirements.

Return the result STRICTLY as a JSON object containing a single key "matches", which is a list of objects. Each object in the list must correspond to the input jobs, using the same "id":
{{
  "matches": [
    {{
      "id": "job-id-from-input",
      "skills_score": 80,
      "experience_score": 70,
      "education_score": 90,
      "overall_score": 79,
      "summary": "Candidate has strong backend experience matching FastAPI and Python requirements. However, they lack the Docker containerization skills.",
      "matched_keywords": ["Python", "FastAPI", "SQL"],
      "missing_keywords": ["Docker", "Kubernetes"],
      "strengths": ["3+ years of python development", "Experience building REST APIs"],
      "weaknesses": ["No containerization experience listed", "No CI/CD pipeline experience"]
    }},
    ...
  ]
}}
Do NOT output any markdown blocks like ```json or any other text before/after the JSON. Just the raw JSON object.
"""
        last_error = None
        for model in self.models:
            logger.info(f"Attempting batch match analysis with model {model}...")
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                raw_text = response.text.strip()
                result = json.loads(raw_text)
                
                # Map by job ID for easy lookup
                matches_map = {}
                for match in result.get("matches", []):
                    job_id = match.get("id")
                    if job_id:
                        # Provide defaults for missing keys
                        defaults = {
                            "skills_score": 50,
                            "experience_score": 50,
                            "education_score": 50,
                            "overall_score": 50,
                            "match_score": 50,
                            "summary": "Match computed successfully.",
                            "matched_keywords": [],
                            "missing_keywords": [],
                            "strengths": [],
                            "weaknesses": []
                        }
                        for k, v in defaults.items():
                            if k not in match:
                                match[k] = v
                        # Ensure match_score mirrors overall_score for backwards compatibility
                        if "overall_score" in match:
                            match["match_score"] = match["overall_score"]
                        matches_map[job_id] = match
                return matches_map
                
            except Exception as e:
                logger.warning(f"Gemini batch match analysis failed with model {model}: {str(e)}")
                last_error = e

        logger.error(f"All Gemini models failed for batch match analysis. Last error: {str(last_error)}", exc_info=True)
        return {}

    @staticmethod
    def compute_ats_keyword_hit_rate(resume_text: str, matched_keywords: list, missing_keywords: list) -> dict:
        """
        Performs a literal case-insensitive scan of the resume for each keyword
        from the matched and missing keyword lists.
        Returns a dict with hit_rate percentage and per-keyword hit details.
        """
        import re
        resume_lower = resume_text.lower()
        
        all_keywords = list(set(matched_keywords + missing_keywords))
        if not all_keywords:
            return {"ats_keyword_hit_rate": 100, "keyword_hits": {}}
        
        keyword_hits = {}
        hits = 0
        for kw in all_keywords:
            # Use word boundary matching for accuracy
            pattern = re.compile(r'\b' + re.escape(kw.lower()) + r'\b')
            count = len(pattern.findall(resume_lower))
            keyword_hits[kw] = count
            if count > 0:
                hits += 1
        
        hit_rate = round((hits / len(all_keywords)) * 100) if all_keywords else 100
        return {
            "ats_keyword_hit_rate": hit_rate,
            "keyword_hits": keyword_hits
        }

if __name__ == "__main__":
    # Test if script runs and accesses Gemini successfully
    # Ensure GEMINI_API_KEY is loaded via environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    try:
        matcher = JobMatcher()
        mock_resume = "Skills: Python, Django, HTML, CSS, JavaScript. Experience: 2 years building web apps."
        mock_job_title = "Python Django Backend Developer"
        mock_job_desc = "Requirements: Python, Django, REST APIs, PostgreSQL. Desirable: Docker."
        
        result = matcher.analyze_match(mock_resume, mock_job_title, mock_job_desc)
        print("Analysis Result:")
        print(json.dumps(result, indent=2))
    except Exception as ex:
        print("Test failed:", ex)
