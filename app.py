"""PR Status menubar app — NSStatusItem + NSPopover + WKWebView shell.

Pure logic lives in core.py. This file is AppKit plumbing only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAppearance,
    NSAppearanceNameVibrantDark,
    NSMenu,
    NSMenuItem,
    NSMinYEdge,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSViewController,
)
from Foundation import (
    NSMakeRect,
    NSMakeSize,
    NSObject,
    NSTimer,
    NSURL,
    NSUserNotification,
    NSUserNotificationCenter,
)
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

import core

POPOVER_WIDTH = 360
POPOVER_MIN_HEIGHT = 200
POPOVER_MAX_HEIGHT = 640


class WebViewController(NSViewController):
    """Hosts the WKWebView; bridge messages go to the app delegate."""

    def initWithBridge_(self, bridge):
        self = objc.super(WebViewController, self).init()
        if self is None:
            return None
        self._bridge = bridge
        return self

    def loadView(self):
        config = WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(
            self._bridge, "app"
        )
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_MAX_HEIGHT), config
        )
        webview.setValue_forKey_(False, "drawsBackground")
        ui = core.ui_path()
        webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(str(ui / "index.html")),
            NSURL.fileURLWithPath_(str(ui)),
        )
        self.setView_(webview)


class AppDelegate(NSObject):
    # ---------- lifecycle ----------

    def applicationDidFinishLaunching_(self, _notification):
        self._fetching = False
        self._ui_ready = False
        self._last_state = None
        self._last_refresh = None
        self._first_run = True
        self._seen_review = set()
        self._seen_mine_reviews = {}
        self._popover = None
        self._timer = None

        cfg = core.load_app_config()
        try:
            self._poll_interval = max(
                10, int(cfg.get("poll_interval_seconds", core.DEFAULT_POLL_INTERVAL))
            )
        except (TypeError, ValueError):
            self._poll_interval = core.DEFAULT_POLL_INTERVAL

        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.button().setTitle_("PR …")

        if not (core.ui_path() / "index.html").exists():
            self._install_fallback_menu("ui/ assets missing — reinstall app")
            return

        button = self._status_item.button()
        button.setTarget_(self)
        button.setAction_("togglePopover:")

        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark)
        )
        self._popover.setContentSize_(NSMakeSize(POPOVER_WIDTH, 420))
        self._popover.setContentViewController_(
            WebViewController.alloc().initWithBridge_(self)
        )

        NSUserNotificationCenter.defaultUserNotificationCenter().setDelegate_(self)

        self._restart_timer()
        self.startFetch_(None)

    def _install_fallback_menu(self, message: str) -> None:
        """Never leave the app unquittable: plain menu with error + Quit."""
        self._status_item.button().setTitle_("PR ⚠️")
        menu = NSMenu.alloc().init()
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(message, None, "")
        )
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit", "terminate:", "q"
            )
        )
        self._status_item.setMenu_(menu)

    def _restart_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            float(self._poll_interval), self, "startFetch:", None, True
        )

    # ---------- popover ----------

    def togglePopover_(self, _sender):
        if self._popover.isShown():
            self._popover.close()
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        button = self._status_item.button()
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSMinYEdge
        )

    # ---------- fetch pipeline (Task 4 fills these in) ----------

    def startFetch_(self, _sender):
        pass

    # ---------- JS bridge ----------

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        body = message.body()
        try:
            action = body.get("action")
        except AttributeError:
            return
        if action == "ready":
            self._ui_ready = True
            if self._last_state is not None:
                self._render(self._last_state)

    @objc.python_method
    def _render(self, state: dict) -> None:
        self._last_state = state
        if not self._ui_ready or self._popover is None:
            return
        js = f"window.render({json.dumps(state)})"
        webview = self._popover.contentViewController().view()
        webview.evaluateJavaScript_completionHandler_(js, None)

    def _push_js(self, js: str) -> None:
        if not self._ui_ready or self._popover is None:
            return
        webview = self._popover.contentViewController().view()
        webview.evaluateJavaScript_completionHandler_(js, None)

    # ---------- notifications (Task 6 fills these in) ----------

    def userNotificationCenter_shouldPresentNotification_(self, _center, _note):
        return True


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        result = subprocess.run(
            [str(core.script_path()), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=core.SCRIPT_TIMEOUT_SECONDS,
            env=core.subprocess_env(),
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        json.loads(result.stdout)
        print("OK")
        sys.exit(0)
    main()
