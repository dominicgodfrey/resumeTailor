"""Step 1 verification: render a sample resume, compile with Tectonic, and
assert it fits on one page while reporting the remaining space.

Run:  python scripts/verify_step1.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.render import compile_and_measure  # noqa: E402

# Sample context mirrors the planned content model. Bullet/`text` fields are
# raw LaTeX (note the escaped & in "Texas A\&M") — the author owns escaping.
SAMPLE = {
    "name": "Jake Ryan",
    "contacts": [
        {"text": "123-456-7890"},
        {"text": "jake@su.edu", "href": "mailto:jake@su.edu"},
        {"text": "linkedin.com/in/jake", "href": "https://linkedin.com/in/jake"},
        {"text": "github.com/jake", "href": "https://github.com/jake"},
    ],
    "education": [
        {"school": "Southwestern University", "location": "Georgetown, TX",
         "degree": "Bachelor of Arts in Computer Science, Minor in Business",
         "dates": "Aug. 2018 -- May 2021"},
        {"school": "Blinn College", "location": "Bryan, TX",
         "degree": "Associate's in Liberal Arts", "dates": "Aug. 2014 -- May 2018"},
    ],
    "experience": [
        {"title": "Undergraduate Research Assistant", "dates": "June 2020 -- Present",
         "company": "Texas A\\&M University", "location": "College Station, TX",
         "bullets": [
             "Developed a REST API using FastAPI and PostgreSQL to store data from learning management systems",
             "Developed a full-stack web application using Flask, React, PostgreSQL and Docker to analyze GitHub data",
             "Explored ways to visualize GitHub collaboration in a classroom setting",
         ]},
        {"title": "Information Technology Support Specialist", "dates": "Sep. 2018 -- Present",
         "company": "Southwestern University", "location": "Georgetown, TX",
         "bullets": [
             "Communicate with managers to set up campus computers used on campus",
             "Assess and troubleshoot computer problems brought by students, faculty and staff",
             "Maintain upkeep of computers, classroom equipment, and 200 printers across campus",
         ]},
        {"title": "Artificial Intelligence Research Assistant", "dates": "May 2019 -- July 2019",
         "company": "Southwestern University", "location": "Georgetown, TX",
         "bullets": [
             "Explored methods to generate video game dungeons based off of \\emph{The Legend of Zelda}",
             "Developed a game in Java to test the generated dungeons",
             "Contributed 50K+ lines of code to an established codebase via Git",
         ]},
    ],
    "projects": [
        {"name": "Gitlytics", "tech": "Python, Flask, React, PostgreSQL, Docker",
         "dates": "June 2020 -- Present",
         "bullets": [
             "Developed a full-stack web application using Flask serving a REST API with React as the frontend",
             "Implemented GitHub OAuth to get data from user's repositories",
             "Visualized GitHub data to show collaboration",
         ]},
        {"name": "Simple Paintball", "tech": "Spigot API, Java, Maven, TravisCI, Git",
         "dates": "May 2018 -- May 2020",
         "bullets": [
             "Developed a Minecraft server plugin to entertain kids during free time for a previous job",
             "Published plugin to websites gaining 2K+ downloads and an average 4.5/5-star review",
         ]},
    ],
    "skills": [
        {"category": "Languages", "items": "Java, Python, C/C++, SQL (Postgres), JavaScript, HTML/CSS, R"},
        {"category": "Frameworks", "items": "React, Node.js, Flask, JUnit, WordPress, Material-UI, FastAPI"},
        {"category": "Developer Tools", "items": "Git, Docker, TravisCI, Google Cloud Platform, VS Code, IntelliJ"},
        {"category": "Libraries", "items": "pandas, NumPy, Matplotlib"},
    ],
}


def main() -> int:
    build_dir = Path(__file__).resolve().parents[1] / "build" / "verify_step1"
    result = compile_and_measure(SAMPLE, build_dir=build_dir)
    print("PDF:     ", result.pdf_path)
    print("Pages:   ", result.pages)
    print("textheight pt:", result.textheight_pt)
    print("used pt: ", round(result.used_pt, 1) if result.used_pt else None)
    print("Status:  ", result.status)
    if not result.fits:
        print("FAIL: expected a single page.")
        return 1
    if result.remaining_cm is None:
        print("WARN: PDF is one page but the zref fit probe did not resolve.")
        return 2
    print("OK: one page, fit probe resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
