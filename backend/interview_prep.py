import os
import json
import logging
from typing import Dict, Any
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class InterviewPrepGenerator:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash", "gemini-2.0-flash"]

    def generate_interview_questions(self, resume_text: str, job_title: str, job_desc: str, weaknesses: list) -> Dict[str, Any]:
        """
        Generates interview questions based on the candidate's resume, the target job, and identified gaps.
        Returns a dict with categorized questions and suggested approaches.
        """
        logger.info(f"Generating interview prep for '{job_title}'")
        
        weaknesses_str = "\n".join(f"- {w}" for w in weaknesses) if weaknesses else "None identified."

        prompt = f"""
You are an expert technical recruiter and interview coach. Your task is to prepare a candidate for an upcoming interview.

=== Target Job Details ===
Title: {job_title}
Description:
{job_desc}

=== Candidate Resume ===
{resume_text}

=== Identified Gaps/Weaknesses ===
{weaknesses_str}

=== Instructions ===
Based on the job description, the candidate's background, and the identified gaps, generate 8-10 likely interview questions categorized as: "Technical", "Behavioral", and "Gap-Probing".
For each question, provide a short "suggested_approach" on how the candidate should answer based on their actual resume experience, avoiding any fabrication.

Return the result STRICTLY as a JSON object with this exact key:
{{
  "questions": [
    {{
      "category": "Technical",
      "question": "Can you explain how you designed the REST APIs mentioned in your resume?",
      "suggested_approach": "Focus on the Python/FastAPI project. Explain your design choices, how you handled authentication, and emphasize the scalability."
    }},
    {{
      "category": "Gap-Probing",
      "question": "I see you don't have direct Docker experience. How would you approach containerizing an app?",
      "suggested_approach": "Acknowledge the gap honestly, but express eagerness to learn. Mention your strong Linux and backend fundamentals which make learning Docker easier."
    }}
  ]
}}
Do NOT output any markdown blocks like ```json or any other text before/after the JSON. Just the raw JSON.
"""
        last_error = None
        for model in self.models:
            logger.info(f"Attempting interview prep generation with model {model}...")
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
                
                if "questions" not in result:
                    result["questions"] = []
                    
                return result

            except Exception as e:
                logger.warning(f"Gemini interview prep generation failed with model {model}: {str(e)}")
                last_error = e

        logger.error(f"All Gemini models failed for interview prep. Last error: {str(last_error)}", exc_info=True)
        return {
            "questions": [
                {
                    "category": "System Error",
                    "question": "Could not generate questions.",
                    "suggested_approach": f"API call failed: {str(last_error)}"
                }
            ]
        }

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    try:
        prep = InterviewPrepGenerator()
        mock_resume = "Skills: Python, Django, HTML, CSS, JavaScript. Experience: 2 years building web apps."
        mock_job_title = "Python Django Backend Developer"
        mock_job_desc = "Requirements: Python, Django, REST APIs, PostgreSQL. Desirable: Docker."
        
        result = prep.generate_interview_questions(mock_resume, mock_job_title, mock_job_desc, ["No Docker experience"])
        print("Interview Prep Output:")
        print(json.dumps(result, indent=2))
    except Exception as ex:
        print("Test failed:", ex)
