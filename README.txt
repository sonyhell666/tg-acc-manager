TG Manager update package

To publish an update, upload either the two files in this folder to the repository root:
sonyhell666/tg-acc-manager

You can upload the two files inside this folder directly to the repository root:
app.py
tg-manager.ico

Alternatively, upload this folder itself and keep these paths:
github-update/app.py
github-update/tg-manager.ico

The application update button supports either layout. It downloads the app and
icon from the same location in the latest commit on branch main. To publish a
new version, run build_github_update.ps1 and upload the resulting files using
one of the layouts above.

This package intentionally excludes data, Telegram sessions, settings, API
credentials, and other local files.

To refresh app.py in this package from the project source, run:
build_github_update.ps1
