# Notes for the project

## 2026-04-02 PS

## Environment

Running on Mac OS 26.3.1 (a)

I added uv (project manager) and ruff (linter) to my instance of the project.

> $>brew install uv ruff

NOTE: uv init will create several files including a main.py file. Only check in those files that you
have created.

Other python environment tools can be used. Do not add the artifacts of the tools to the project.

## Scheduling The Script

I did the following:
* Created a trivial shell script to run the application fetch command run-fetch.sh
* Changed the permissions of the script to allow execution.
* Added run-fetch.sh to crontab

For reference, here is the script:

```aiignore
#!/opt/homebrew/bin/bash
# A shell wrapper around the brew_analytics.py 'fetch' command for inclusion in crontab

/opt/homebrew/bin/uv run [path to working directory]/brew_analytics.py fetch
```                                               

There are other ways to schedule the script, including using systemd or on Windows, the Task Scheduler.

To test your script, here is an example crontab entry: run the script every minute, append output to run-fetch.log, and redirect errors to the log.

```
 */1 * * * * [full path to script]/run-fetch.sh >> [full path to log file]/run-fetch.log 2>&1
```

Examine the log file to ensure that the script is running as expected.

To run your script in production (eg. run every day at 11:00):

```
> 0 11 * * * [full path to script]/run-fetch.sh >> [full path to log file]/run-fetch.log 2>&1
```

## Design Notes