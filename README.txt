TG Manager update package

Upload this entire folder to the root of the GitHub repository:
sonyhell666/tg-acc-manager

Keep these exact paths in GitHub:
github-update/app.py
github-update/tg-manager.ico

The application update button reads github-update/app.py and
github-update/tg-manager.ico from the latest commit on branch main. To publish
a new app version and icon, run build_github_update.ps1 in the project folder
and commit/push this folder to main.

This package intentionally excludes data, Telegram sessions, settings, API
credentials, and other local files.

To refresh app.py in this package from the project source, run:
build_github_update.ps1
