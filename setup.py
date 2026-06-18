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
    "arch": "arm64",
    "plist": {
        "CFBundleName": "PR Status",
        "CFBundleDisplayName": "PR Status",
        "CFBundleIdentifier": "com.github.dtorsson.pr-status",
        "CFBundleVersion": "0.2.1",
        "CFBundleShortVersionString": "0.2.1",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "",
        "NSUserNotificationAlertStyle": "banner",
    },
    "includes": ["core"],
    "excludes": ["tkinter", "_tkinter"],
    "resources": ["pr-status", "ui"],
}

setup(
    name="PR Status",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
