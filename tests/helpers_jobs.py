"""Job-alert fixtures (made-up jobs in LinkedIn's alert format) and a stand-in scoring model."""

import re
from typing import Any

from langchain_core.messages import AIMessage

ALERT = """Your job alert for water engineer in Hong Kong
Senior Water Engineer
Arup
Hong Kong SAR

Actively recruiting
View job: https://www.linkedin.com/comm/jobs/view/4100000001/?trackingId=abc%3D&refId=x

---------------------------------------------------------

Research Assistant Professor
The Hong Kong Polytechnic University
Hung Hom, Kowloon, Hong Kong SAR

1 school alum
Easy Apply
View job: https://www.linkedin.com/comm/jobs/view/4100000002/?trackingId=def

---------------------------------------------------------

Hydraulic Modeller
Jacobs
View job: https://www.linkedin.com/jobs/view/4100000003/

---------------------------------------------------------

See all jobs on LinkedIn:  https://www.linkedin.com/comm/jobs/search-results/?keywords=water
Manage your job alerts:  https://www.linkedin.com/comm/jobs/alerts?lipi=x
"""

# A second alert repeating one job and adding one.
ALERT_2 = """Your job alert for Hong Kong
Senior Water Engineer
Arup
Hong Kong SAR
View job: https://www.linkedin.com/comm/jobs/view/4100000001/?trackingId=zzz

---------------------------------------------------------

Investment Associate
Some Fund
Hong Kong SAR
View job: https://www.linkedin.com/comm/jobs/view/4100000004/?trackingId=yyy
"""


# Layout A: a heading paragraph before the first job.
ALERT_HEADED = """Your job alert for Hong Kong
New jobs match your preferences.
Manage alerts: https://www.linkedin.com/comm/jobs/alerts?lipi=abc

Technical Officer
Jacobs
Hong Kong

3 connections
View job: https://www.linkedin.com/comm/jobs/view/4100000005/?trackingId=a
"""

# Layout B: links, then the alert's name with leftover HTML tags.
ALERT_HTMLISH = """See all jobs https://www.linkedin.com/jobs/search-results/?x=1
Edit alert https://www.linkedin.com/jobs/jam/manage/?y=2


<strong class="font-bold" style="font-weight: 600;">water engineer</strong> jobs in Hong Kong

Senior Hydraulic Engineer
AECOM
Hong Kong

This company is actively hiring
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/4100000006/?trackingId=b
"""


class FakeRater:
    """Stands in for the model: scores by keyword, one "J12 | 85 | reason" line per job."""

    SCORES = {"Water": 92, "Professor": 78, "Hydraulic": 64}

    def invoke(self, messages: list[Any]) -> AIMessage:
        lines = []
        for ref, line in re.findall(r"^J(\d+)( \|.*)$", messages[-1].content, re.M):
            score = next((s for word, s in self.SCORES.items() if word in line), 20)
            lines.append(f"**J{ref}** | {score} | keyword fit {score}")
        return AIMessage("Here are the scores:\n" + "\n".join(lines))
