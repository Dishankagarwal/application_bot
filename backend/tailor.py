import os
import json
import logging
from typing import Dict, Any
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResumeTailor:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash", "gemini-2.0-flash"]

    def tailor_resume(self, resume_text: str, job_title: str, job_desc: str) -> Dict[str, Any]:
        """
        Queries Gemini to optimize resume content for a specific job description.
        Returns:
            - tailored_resume: Full updated resume content (in Markdown format).
            - changes_made: List of objects showing section updated and the rationale.
        """
        logger.info(f"Generating tailored resume for '{job_title}'")
        
        prompt = f"""
You are an expert resume writer. Your job is to adapt the candidate's resume to highlight their relevance to the target job description. 
IMPORTANT: Maintain absolute honesty. DO NOT fabricate qualifications, projects, or work history. Instead, rephrase existing descriptions, prioritize matching skills, and adapt the professional summary to show alignment with the job requirements.

=== Target Job Details ===
Title: {job_title}
Description:
{job_desc}

=== Candidate Resume ===
{resume_text}

=== Instructions ===
1. Rewrite the professional summary to directly address how the candidate's background matches this specific role.
2. Reorder/optimize the skills list to highlight top skills required by the job.
3. Rephrase bullets in the Professional Experience section to emphasize tasks, tools, and achievements that correspond to key job responsibilities.
4. Keep the original structure (Education, Contact info, Experience) intact.
5. Provide a summary list of changes made.

Return the result STRICTLY as a JSON object with this exact keys:
{{
  "tailored_resume_markdown": "Full updated resume in clean, well-formatted Markdown...",
  "changes_made": [
    {{
      "section": "Professional Summary",
      "change": "Rewrote to focus on backend web services and python experience matching the job requirement.",
      "rationale": "Aligns the introduction with the primary tech stack requested."
    }},
    {{
      "section": "Skills List",
      "change": "Moved Python and Django to the front of the list, grouped databases together.",
      "rationale": "Makes critical technical keywords immediately visible to ATS and recruiters."
    }}
  ]
}}
Do NOT output any markdown blocks like ```json or any other text before/after the JSON. Just the raw JSON.
"""
        last_error = None
        for model in self.models:
            logger.info(f"Attempting resume tailoring with model {model}...")
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
                
                # Verify keys
                if "tailored_resume_markdown" not in result:
                    result["tailored_resume_markdown"] = resume_text
                if "changes_made" not in result:
                    result["changes_made"] = []
                    
                return result

            except Exception as e:
                logger.warning(f"Gemini resume tailoring failed with model {model}: {str(e)}")
                last_error = e

        logger.error(f"All Gemini models failed for resume tailoring. Last error: {str(last_error)}", exc_info=True)
        return {
            "tailored_resume_markdown": resume_text,
            "changes_made": [
                {
                    "section": "System Error",
                    "change": "None",
                    "rationale": f"API call failed: {str(last_error)}"
                }
            ]
        }

    def generate_cover_letter(self, resume_text: str, job_title: str, job_desc: str, company: str) -> Dict[str, Any]:
        """
        Generates a tailored cover letter using the candidate's resume and job details.
        Returns:
            - cover_letter_text: The generated cover letter.
            - key_points_highlighted: List of points emphasized.
        """
        logger.info(f"Generating cover letter for '{job_title}' at '{company}'")
        
        prompt = f"""
You are an expert career coach and cover letter writer. Write a professional, targeted cover letter for the candidate applying to the job below.
IMPORTANT: Strictly use the candidate's actual resume to pull real achievements and skills. DO NOT fabricate any experience, skills, or metrics.

=== Target Job Details ===
Title: {job_title}
Company: {company}
Description:
{job_desc}

=== Candidate Resume ===
{resume_text}

=== Instructions ===
1. Write a compelling 3-4 paragraph cover letter.
2. Address it to the Hiring Manager.
3. Highlight 2-3 specific achievements or skills from the resume that directly align with the job description.
4. Keep the tone professional, confident, and enthusiastic.
5. Also provide a summary list of the key points you highlighted.

Return the result STRICTLY as a JSON object with this exact key:
{{
  "cover_letter_text": "Dear Hiring Manager,\\n\\nI am writing to express my interest in the...",
  "key_points_highlighted": [
    "Highlighted 3 years of Python experience matching backend requirements.",
    "Emphasized REST API design achievements."
  ]
}}
Do NOT output any markdown blocks like ```json or any other text before/after the JSON. Just the raw JSON.
"""
        last_error = None
        for model in self.models:
            logger.info(f"Attempting cover letter generation with model {model}...")
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
                
                if "cover_letter_text" not in result:
                    result["cover_letter_text"] = "Failed to generate cover letter."
                if "key_points_highlighted" not in result:
                    result["key_points_highlighted"] = []
                    
                return result

            except Exception as e:
                logger.warning(f"Gemini cover letter generation failed with model {model}: {str(e)}")
                last_error = e

        logger.error(f"All Gemini models failed for cover letter generation. Last error: {str(last_error)}", exc_info=True)
        return {
            "cover_letter_text": f"API call failed: {str(last_error)}",
            "key_points_highlighted": []
        }

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    try:
        tailor = ResumeTailor()
        mock_resume = "Summary: Frontend dev with React. Skills: React, CSS, HTML. Experience: React Developer at ABC."
        mock_job_title = "Senior React Frontend Engineer"
        mock_job_desc = "Requirements: React, Hooks, Redux, Performance optimization."
        
        result = tailor.tailor_resume(mock_resume, mock_job_title, mock_job_desc)
        print("Tailored Resume Output:")
        print(json.dumps(result, indent=2))
    except Exception as ex:
        print("Test failed:", ex)
