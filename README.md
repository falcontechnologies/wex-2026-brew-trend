# wex 2026 homebrew trending

## Quick Summary
This repository is a tool to gather and report on trending homebrew from the api

[Homebrew Formulae Install Events 30 days)[https://formulae.brew.sh/analytics/install/30d/]

and storing the results to a local SQLite database.

## Environment Setup

This uses Python, Flask and SQLite with an ML coding tool to perform data capture and storage and a web application to display the trend graph

TBD: Determine more requirements for the web application

Python should be 3.11 or greater.

No virtual environment is enforced in this project. Research, make a working instruction and use an appropriate virtual environment. In the instruction, include why you chose the virtual enviornment tool and a link to the installation page and documentation page. You can check in the working instructions to your branch of this project.

No restriction is placed on using a coding ML assistant. Claude Sonnet 4.6 and Claude Code have both been tried. Other ML code assistants can be used.

## Application Information

The project expects to take a snapshot of the data at the above endpoint on a scheduled basis and store it to a local SQLite database. The project may, but does not require a scheduler, only that the use of it should be documented. Scheduling may use cron, systemd or other techniques to provide the scheduling.

The data should be written to a table in the database and the event when it was run should be stored somewhere in the database.

Once some data has been collected over a period of time, a separate web application is required to allow a user to interogate the data to provide a daily installation snapshot of which packages are in high use. The application is missing requirements, so some experimentation is expected to clarify the requirements.

The questions to be answered are
* What are the 10 most popular packages over a period of time?
* Which packages are trending up? Criteria is needed to separate daily differences from noise.
* Which packages are trending down?
* For any individual package, where does it rank against all other packages and what total daily download for the last day? Do we need to provide an average value over the next time period as well?

## Known Unknowns

The data from the api listed above needs to be analyzed to determine the best way to create the daily information. The initial analysis suggests that creating a difference table between two record rows of the same formula name by date should provide synthetic data that is good enough to provide the trend line for packages.

Required packages (Python) have not been set. These packages must be included in the requirements.txt file and bound to a major version.

## git Workflow

A branch per developer will be used off of the main branch.

## ML Coding Assistant

Current practise has a Markdown file to provide context for the tool to focus the work.

Some preliminary standards are, for Claude, to have both an AGENTS.md and a CLAUDE.md file. The AGENTS.md file has general context information while CLAUDE.md has Claude specific context. Other ML coding tools may have different requirements. Refer to the tool documentation to create the correct file and include the file in your repository assets.

To address the limitations of context windows, when necessary add a MEMORY.md file that will contain a tool summarized prompt collection that can be used to bring the ML conding tool to a project-aware state with room to add additional prompts. If you use this, remember to check in the file to git.

To provide trace-ability, please create a prompt session file or directory and check it in to git.
 
----
