TG Manager update package

Upload this entire folder to the root of the GitHub repository:
sonyhell666/tg-acc-manager

Keep this exact path in GitHub:
github-update/app.py

The application update button reads only github-update/app.py from the latest
commit on branch main. To publish a new app version, replace app.py in this
folder with the current project app.py and commit/push the folder to main.

This package intentionally excludes data, Telegram sessions, settings, API
credentials, and other local files.

To refresh app.py in this package from the project source, run:
build_github_update.ps1
