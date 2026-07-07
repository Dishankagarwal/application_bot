import os
import markdown
from xhtml2pdf import pisa

def main():
    resume_markdown = """
# John Doe
Email: john.doe@example.com | Phone: 555-0199 | Location: Austin, TX

## Professional Summary
Dedicated and results-oriented Software Engineer with over 3 years of professional experience designing, building, and maintaining high-performance backend systems. Proven track record of developing RESTful APIs, optimizing database queries, and collaborating with cross-functional teams to deliver scalable software solutions.

## Skills
* **Languages**: Python, SQL, JavaScript, HTML, CSS
* **Frameworks**: FastAPI, Django, Flask, React
* **Databases**: PostgreSQL, MySQL, SQLite, Redis
* **Tools & DevOps**: Git, Docker, Postman

## Professional Experience
### Software Engineer | TechCorp Inc. (Austin, TX)
*June 2023 - Present*
* Designed and built scalable REST APIs using **FastAPI** and **Django**, reducing API response times by 25%.
* Managed and optimized **PostgreSQL** database schemas and complex queries.
* Implemented user authentication and authorization using OAuth2 and JWT.
* Developed interactive frontend features using **React** and TailwindCSS.

### Junior Python Developer | WebDesign LLC (Dallas, TX)
*July 2021 - May 2023*
* Maintained legacy backend systems built with Flask.
* Developed web scraping scripts to extract and normalize job postings data.
* Wrote unit and integration tests, increasing coverage from 60% to 85%.

## Education
### Bachelor of Science in Computer Science
*University of Texas at Austin* (Graduated May 2021)
"""
    html = markdown.markdown(resume_markdown)
    output_filename = "test_resume.pdf"
    
    with open(output_filename, "wb") as pdf_file:
        pisa_status = pisa.CreatePDF(html, dest=pdf_file)
        if pisa_status.err:
            print(f"Failed to generate PDF: {pisa_status.err}")
        else:
            print(f"Successfully generated {output_filename}")

if __name__ == "__main__":
    main()
