"""py2app build configuration for PR Status menubar app.

Build:    python3 setup.py py2app
Dev run:  python3 setup.py py2app -A   (alias mode, live source)
Output:   dist/PR Status.app
"""
from setuptools import setup

APP = ["app.py"]
DATA_FILES = ["pr-status"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "PR Status",
        "CFBundleDisplayName": "PR Status",
        "CFBundleIdentifier": "com.github.dtorsson.pr-status",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "",
        "NSUserNotificationAlertStyle": "banner",
    },
    "packages": ["rumps"],
    "resources": ["pr-status"],
}

setup(
    name="PR Status",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
