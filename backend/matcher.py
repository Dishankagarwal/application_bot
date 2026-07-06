import os
import json
import logging
from typing import Dict, Any
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
        self.model_name = "gemini-2.5-flash"

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
1. `match_score`: An integer from 0 to 100 representing how well the candidate fits the job.
2. `summary`: A concise 2-sentence summary explaining why they are or are not a good fit.
3. `matched_keywords`: Key technical skills/keywords that exist in both the job description and the resume.
4. `missing_keywords`: Important keywords/skills listed in the job description that are missing from the resume.
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
        try:
            # We can use the Structured JSON feature in Gemini GenAI SDK:
            response = self.client.models.generate_content(
                model=self.model_name,
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
            logger.error(f"Gemini match analysis failed: {str(e)}", exc_info=True)
            return {
                "match_score": 0,
                "summary": f"Failed to perform matching: {str(e)}",
                "matched_keywords": [],
                "missing_keywords": [],
                "strengths": [],
                "weaknesses": ["Error in Gemini analysis"]
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
