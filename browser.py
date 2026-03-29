#!/usr/bin/env python3
"""Full-featured tabbed Python web browser with multimedia support.

Main features
-------------
- Tabbed browsing
- HTML5 audio/video playback through Qt WebEngine
- Fullscreen video support
- Downloads with Save As dialog
- Persistent cookies, cache, history, and bookmarks
- Find in page
- Open local files
- Save current page as PDF
- Basic site permission prompts
- New windows/tabs opened from websites are redirected into tabs

Tested for syntax with Python 3.11.
Requires PySide6.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

APP_NAME = "PyMediaBrowser"
HOME_URL = "https://www.google.com"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def readable_url(text: str) -> QUrl:
    text = text.strip()
    if not text:
        return QUrl(HOME_URL)

    guess = QUrl.fromUserInput(text)
    if guess.isValid() and guess.scheme():
        return guess

    if "." in text and " " not in text:
        return QUrl.fromUserInput(f"https://{text}")

    return QUrl(f"https://www.google.com/search?q={quote_plus(text)}")


class AppState:
    """Manage persistent JSON-backed browser state."""

    def __init__(self) -> None:
        config_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        )
        data_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        download_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        )

        self.config_dir = ensure_dir(config_root / APP_NAME)
        self.data_dir = ensure_dir(data_root / APP_NAME)
        self.downloads_dir = ensure_dir(download_root)
        self.profile_dir = ensure_dir(self.data_dir / "profile")

        self.history_file = self.config_dir / "history.json"
        self.bookmarks_file = self.config_dir / "bookmarks.json"
        self.settings_file = self.config_dir / "settings.json"

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _save_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load_history(self) -> list[dict[str, str]]:
        history = self._load_json(self.history_file, [])
        if isinstance(history, list):
            return [entry for entry in history if isinstance(entry, dict)]
        return []

    def save_history(self, history: list[dict[str, str]]) -> None:
        self._save_json(self.history_file, history[-500:])

    def load_bookmarks(self) -> list[dict[str, str]]:
        bookmarks = self._load_json(self.bookmarks_file, [])
        if isinstance(bookmarks, list):
            return [entry for entry in bookmarks if isinstance(entry, dict)]
        return []

    def save_bookmarks(self, bookmarks: list[dict[str, str]]) -> None:
        unique: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in bookmarks:
            title = str(item.get("title", "")).strip() or "Untitled"
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            key = (title, url)
            if key not in seen:
                unique.append({"title": title, "url": url})
                seen.add(key)
        self._save_json(self.bookmarks_file, unique[:500])

    def load_settings(self) -> dict[str, Any]:
        settings = self._load_json(self.settings_file, {})
        if isinstance(settings, dict):
            return settings
        return {}

    def save_settings(self, settings: dict[str, Any]) -> None:
        self._save_json(self.settings_file, settings)


class BrowserPage(QWebEnginePage):
    def __init__(self, window: "BrowserMainWindow", profile: QWebEngineProfile) -> None:
        super().__init__(profile, window)
        self.window = window

    def createWindow(self, _window_type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        new_view = self.window.add_browser_tab(QUrl(HOME_URL), switch_to=True)
        return new_view.page()


class BrowserView(QWebEngineView):
    request_close = Signal(QWidget)

    def __init__(self, window: "BrowserMainWindow", profile: QWebEngineProfile) -> None:
        super().__init__(window)
        self.window = window
        self.setPage(BrowserPage(window, profile))
        self._configure_settings()
        self._connect_page_signals()

    def _configure_settings(self) -> None:
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)

    def _connect_page_signals(self) -> None:
        page = self.page()
        page.fullScreenRequested.connect(self.window.handle_fullscreen_request)
        page.windowCloseRequested.connect(lambda: self.request_close.emit(self))
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(self._handle_permission_request)

    def _handle_permission_request(self, permission: Any) -> None:
        if hasattr(permission, "isValid") and not permission.isValid():
            return

        origin = "this site"
        if hasattr(permission, "origin"):
            try:
                origin = permission.origin().toString()
            except Exception:
                origin = "this site"

        feature = "permission"
        if hasattr(permission, "permissionType"):
            try:
                feature = str(permission.permissionType()).split(".")[-1]
            except Exception:
                feature = "permission"

        answer = QMessageBox.question(
            self,
            "Website Permission",
            f"Allow {origin} to use: {feature}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        try:
            if answer == QMessageBox.StandardButton.Yes:
                permission.grant()
            else:
                permission.deny()
        except Exception:
            # Keep browsing working even when the API differs across Qt builds.
            pass


class FindBar(QWidget):
    def __init__(self, window: "BrowserMainWindow") -> None:
        super().__init__(window)
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        layout.addWidget(QLabel("Find:"))
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Find in page")
        self.search_edit.returnPressed.connect(self.find_next)
        self.search_edit.textChanged.connect(self.find_next)
        layout.addWidget(self.search_edit, 1)

        prev_btn = QPushButton("Previous", self)
        prev_btn.clicked.connect(self.find_previous)
        layout.addWidget(prev_btn)

        next_btn = QPushButton("Next", self)
        next_btn.clicked.connect(self.find_next)
        layout.addWidget(next_btn)

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.hide_and_clear)
        layout.addWidget(close_btn)

        self.hide()

    def focus_search(self) -> None:
        self.show()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def hide_and_clear(self) -> None:
        view = self.window.current_view()
        if view is not None:
            view.page().findText("")
        self.search_edit.clear()
        self.hide()

    def find_next(self) -> None:
        text = self.search_edit.text()
        view = self.window.current_view()
        if view is None:
            return
        view.page().findText(text)

    def find_previous(self) -> None:
        text = self.search_edit.text()
        view = self.window.current_view()
        if view is None:
            return
        view.page().findText(text, QWebEnginePage.FindFlag.FindBackward)


class HistoryDialog(QDialog):
    def __init__(self, window: "BrowserMainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("History")
        self.resize(700, 450)
        layout = QVBoxLayout(self)

        self.list_widget = QListWidget(self)
        layout.addWidget(self.list_widget)

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self.populate()
        self.list_widget.itemDoubleClicked.connect(self.open_item)

    def populate(self) -> None:
        self.list_widget.clear()
        for item in reversed(self.window.history):
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            lw = QListWidgetItem(f"{title}\n{url}")
            lw.setData(Qt.ItemDataRole.UserRole, url)
            self.list_widget.addItem(lw)

    def open_item(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.window.add_browser_tab(QUrl(url), switch_to=True)
            self.accept()


class DownloadsDialog(QDialog):
    def __init__(self, window: "BrowserMainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Downloads")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget(self)
        layout.addWidget(self.list_widget)
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        for item in reversed(self.window.download_log):
            self.list_widget.addItem(item)


class BrowserMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.history = self.state.load_history()
        self.bookmarks = self.state.load_bookmarks()
        self.download_log: list[str] = []
        self.settings_store = self.state.load_settings()
        self.page_fullscreen_active = False

        self.profile = self._build_profile()

        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.setStatusBar(QStatusBar(self))

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.current_tab_changed)
        self.setCentralWidget(self.tabs)

        self.find_bar = FindBar(self)
        self.addToolBarBreak()
        self.navigation_toolbar = self._build_navigation_toolbar()
        self.bookmarks_toolbar = self._build_bookmarks_toolbar()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.navigation_toolbar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.bookmarks_toolbar)

        self.menuBar().addMenu(self._build_file_menu())
        self.menuBar().addMenu(self._build_edit_menu())
        self.bookmarks_menu = self.menuBar().addMenu("&Bookmarks")
        self.menuBar().addMenu(self._build_view_menu())
        self.menuBar().addMenu(self._build_history_menu())
        self.menuBar().addMenu(self._build_help_menu())
        self.refresh_bookmarks_ui()

        finder_container = QWidget(self)
        finder_layout = QVBoxLayout(finder_container)
        finder_layout.setContentsMargins(0, 0, 0, 0)
        finder_layout.addWidget(self.find_bar)
        self.navigation_toolbar.addWidget(finder_container)

        self._setup_shortcuts()
        self.add_browser_tab(QUrl(self.settings_store.get("last_url", HOME_URL)), switch_to=True)

    def _build_profile(self) -> QWebEngineProfile:
        profile = QWebEngineProfile(APP_NAME, self)
        profile.setPersistentStoragePath(str(self.state.profile_dir))
        profile.setCachePath(str(self.state.profile_dir / "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.downloadRequested.connect(self.handle_download_requested)
        return profile

    def _build_navigation_toolbar(self) -> QToolBar:
        toolbar = QToolBar("Navigation", self)
        toolbar.setMovable(False)

        back_action = QAction("Back", self)
        back_action.setShortcut(QKeySequence.StandardKey.Back)
        back_action.triggered.connect(lambda: self.current_view() and self.current_view().back())
        toolbar.addAction(back_action)

        forward_action = QAction("Forward", self)
        forward_action.setShortcut(QKeySequence.StandardKey.Forward)
        forward_action.triggered.connect(lambda: self.current_view() and self.current_view().forward())
        toolbar.addAction(forward_action)

        reload_action = QAction("Reload", self)
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        reload_action.triggered.connect(lambda: self.current_view() and self.current_view().reload())
        toolbar.addAction(reload_action)

        stop_action = QAction("Stop", self)
        stop_action.triggered.connect(lambda: self.current_view() and self.current_view().stop())
        toolbar.addAction(stop_action)

        home_action = QAction("Home", self)
        home_action.triggered.connect(lambda: self.navigate_to(HOME_URL))
        toolbar.addAction(home_action)

        new_tab_action = QAction("New Tab", self)
        new_tab_action.setShortcut(QKeySequence.StandardKey.AddTab)
        new_tab_action.triggered.connect(lambda: self.add_browser_tab(QUrl(HOME_URL), switch_to=True))
        toolbar.addAction(new_tab_action)

        self.url_bar = QLineEdit(self)
        self.url_bar.setClearButtonEnabled(True)
        self.url_bar.returnPressed.connect(self.load_url_from_bar)
        self.url_bar.setPlaceholderText("Enter a URL or search terms")
        toolbar.addWidget(self.url_bar)

        go_action = QAction("Go", self)
        go_action.triggered.connect(self.load_url_from_bar)
        toolbar.addAction(go_action)

        bookmark_action = QAction("Bookmark", self)
        bookmark_action.triggered.connect(self.add_current_page_bookmark)
        toolbar.addAction(bookmark_action)

        downloads_action = QAction("Downloads", self)
        downloads_action.triggered.connect(self.show_downloads)
        toolbar.addAction(downloads_action)

        return toolbar

    def _build_bookmarks_toolbar(self) -> QToolBar:
        toolbar = QToolBar("Bookmarks", self)
        toolbar.setMovable(False)
        return toolbar

    def _build_file_menu(self) -> QMenu:
        menu = QMenu("&File", self)

        new_tab = QAction("New Tab", self)
        new_tab.setShortcut(QKeySequence.StandardKey.AddTab)
        new_tab.triggered.connect(lambda: self.add_browser_tab(QUrl(HOME_URL), switch_to=True))
        menu.addAction(new_tab)

        open_file = QAction("Open File…", self)
        open_file.setShortcut(QKeySequence.StandardKey.Open)
        open_file.triggered.connect(self.open_local_file)
        menu.addAction(open_file)

        save_pdf = QAction("Save Page as PDF…", self)
        save_pdf.triggered.connect(self.save_page_as_pdf)
        menu.addAction(save_pdf)

        menu.addSeparator()

        close_tab = QAction("Close Tab", self)
        close_tab.setShortcut(QKeySequence.StandardKey.Close)
        close_tab.triggered.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        menu.addAction(close_tab)

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        return menu

    def _build_edit_menu(self) -> QMenu:
        menu = QMenu("&Edit", self)
        find_action = QAction("Find in Page", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.find_bar.focus_search)
        menu.addAction(find_action)
        return menu

    def _build_view_menu(self) -> QMenu:
        menu = QMenu("&View", self)

        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in.triggered.connect(lambda: self.adjust_zoom(0.1))
        menu.addAction(zoom_in)

        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out.triggered.connect(lambda: self.adjust_zoom(-0.1))
        menu.addAction(zoom_out)

        reset_zoom = QAction("Reset Zoom", self)
        reset_zoom.triggered.connect(self.reset_zoom)
        menu.addAction(reset_zoom)

        toggle_fullscreen = QAction("Toggle Fullscreen Window", self)
        toggle_fullscreen.setShortcut(QKeySequence("F11"))
        toggle_fullscreen.triggered.connect(self.toggle_window_fullscreen)
        menu.addAction(toggle_fullscreen)

        return menu

    def _build_history_menu(self) -> QMenu:
        menu = QMenu("&History", self)

        show_history = QAction("Show History", self)
        show_history.triggered.connect(self.show_history)
        menu.addAction(show_history)

        clear_history = QAction("Clear History", self)
        clear_history.triggered.connect(self.clear_history)
        menu.addAction(clear_history)

        return menu

    def _build_help_menu(self) -> QMenu:
        menu = QMenu("&Help", self)
        about = QAction("About", self)
        about.triggered.connect(self.show_about)
        menu.addAction(about)
        return menu

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_url_bar)
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self.show_downloads)
        QShortcut(QKeySequence("Escape"), self, activated=self.exit_page_fullscreen)

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            (
                f"{APP_NAME}\n\n"
                "Tabbed desktop browser built with PySide6 + Qt WebEngine.\n"
                "Supports standard HTML5 browsing plus audio/video playback,\n"
                "downloads, bookmarks, history, local files, and PDF export."
            ),
        )

    def focus_url_bar(self) -> None:
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    def current_view(self) -> BrowserView | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, BrowserView) else None

    def add_browser_tab(self, qurl: QUrl, switch_to: bool = True) -> BrowserView:
        browser = BrowserView(self, self.profile)
        browser.request_close.connect(self.close_widget_tab)
        browser.urlChanged.connect(lambda url, view=browser: self.update_url_bar_from_view(view, url))
        browser.titleChanged.connect(lambda title, view=browser: self.update_tab_title(view, title))
        browser.iconChanged.connect(lambda icon, view=browser: self.update_tab_icon(view, icon))
        browser.loadProgress.connect(self.update_load_progress)
        browser.loadFinished.connect(lambda ok, view=browser: self.record_history_from_view(view, ok))

        index = self.tabs.addTab(browser, "Loading…")
        if switch_to:
            self.tabs.setCurrentIndex(index)
        browser.setUrl(qurl)
        return browser

    def close_widget_tab(self, widget: QWidget) -> None:
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self.close_tab(index)

    def close_tab(self, index: int) -> None:
        if index < 0:
            return
        if self.tabs.count() == 1:
            self.close()
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()

    def current_tab_changed(self, _index: int) -> None:
        view = self.current_view()
        if view is None:
            return
        self.update_url_bar_from_view(view, view.url())
        self.setWindowTitle(f"{view.title() or APP_NAME} - {APP_NAME}")

    def update_tab_title(self, view: BrowserView, title: str) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, (title or "New Tab")[:30])
        if view is self.current_view():
            self.setWindowTitle(f"{title or APP_NAME} - {APP_NAME}")

    def update_tab_icon(self, view: BrowserView, icon: QIcon) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabIcon(index, icon)

    def update_url_bar_from_view(self, view: BrowserView, url: QUrl) -> None:
        if view is self.current_view():
            self.url_bar.setText(url.toString())
            self.url_bar.setCursorPosition(0)

    def update_load_progress(self, value: int) -> None:
        self.statusBar().showMessage(f"Loading… {value}%")
        if value >= 100:
            self.statusBar().showMessage("Ready", 1500)

    def navigate_to(self, text: str) -> None:
        view = self.current_view()
        if view is None:
            return
        view.setUrl(readable_url(text))

    def load_url_from_bar(self) -> None:
        self.navigate_to(self.url_bar.text())

    def open_local_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Open file", str(Path.home()))
        if file_name:
            self.add_browser_tab(QUrl.fromLocalFile(file_name), switch_to=True)

    def save_page_as_pdf(self) -> None:
        view = self.current_view()
        if view is None:
            return
        suggested = (view.title() or "page").replace("/", "_").replace("\\", "_") + ".pdf"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save page as PDF",
            str(self.state.downloads_dir / suggested),
            "PDF files (*.pdf)",
        )
        if not path:
            return

        def callback(ok: bool) -> None:
            if ok:
                self.statusBar().showMessage(f"Saved PDF: {path}", 4000)
            else:
                QMessageBox.warning(self, "Save PDF", "Failed to save the page as PDF.")

        view.page().printToPdf(path, callback)

    def adjust_zoom(self, delta: float) -> None:
        view = self.current_view()
        if view is None:
            return
        new_zoom = max(0.25, min(5.0, view.zoomFactor() + delta))
        view.setZoomFactor(new_zoom)
        self.statusBar().showMessage(f"Zoom: {int(new_zoom * 100)}%", 1500)

    def reset_zoom(self) -> None:
        view = self.current_view()
        if view is None:
            return
        view.setZoomFactor(1.0)
        self.statusBar().showMessage("Zoom: 100%", 1500)

    def toggle_window_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def handle_fullscreen_request(self, request: Any) -> None:
        try:
            toggle_on = bool(request.toggleOn())
        except Exception:
            toggle_on = False

        try:
            request.accept()
        except Exception:
            return

        if toggle_on:
            self.page_fullscreen_active = True
            self.menuBar().hide()
            self.navigation_toolbar.hide()
            self.bookmarks_toolbar.hide()
            self.find_bar.hide()
            self.statusBar().hide()
            self.showFullScreen()
            self.statusBar().showMessage("Press Escape to leave page fullscreen", 5000)
        else:
            self.exit_page_fullscreen()

    def exit_page_fullscreen(self) -> None:
        if not self.page_fullscreen_active:
            return
        self.page_fullscreen_active = False
        self.showNormal()
        self.menuBar().show()
        self.navigation_toolbar.show()
        self.bookmarks_toolbar.show()
        self.statusBar().show()

    def handle_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        suggestion = download.downloadFileName() or "download.bin"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save download",
            str(self.state.downloads_dir / suggestion),
        )
        if not path:
            download.cancel()
            return

        target = Path(path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()

        entry = f"Started: {target.name}"
        self.download_log.append(entry)
        self.statusBar().showMessage(entry, 2500)

        if hasattr(download, "receivedBytesChanged"):
            download.receivedBytesChanged.connect(
                lambda d=download, name=target.name: self.statusBar().showMessage(
                    f"Downloading {name}: {d.receivedBytes()} bytes received", 1000
                )
            )
        if hasattr(download, "isFinishedChanged"):
            download.isFinishedChanged.connect(
                lambda d=download, name=target.name: self._finish_download_entry(d, name)
            )
        elif hasattr(download, "stateChanged"):
            download.stateChanged.connect(
                lambda _state, d=download, name=target.name: self._finish_download_entry(d, name)
            )

    def _finish_download_entry(self, download: QWebEngineDownloadRequest, name: str) -> None:
        if hasattr(download, "isFinished") and not download.isFinished():
            return
        message = f"Finished: {name}"
        self.download_log.append(message)
        self.statusBar().showMessage(message, 4000)

    def show_downloads(self) -> None:
        dialog = DownloadsDialog(self)
        dialog.exec()

    def add_current_page_bookmark(self) -> None:
        view = self.current_view()
        if view is None:
            return
        title = view.title() or view.url().toString() or "Untitled"
        url = view.url().toString()
        if not url:
            return
        self.bookmarks.append({"title": title, "url": url})
        self.state.save_bookmarks(self.bookmarks)
        self.bookmarks = self.state.load_bookmarks()
        self.refresh_bookmarks_ui()
        self.statusBar().showMessage(f"Bookmarked: {title}", 2500)

    def refresh_bookmarks_ui(self) -> None:
        self.bookmarks_menu.clear()
        self.bookmarks_toolbar.clear()

        add_action = QAction("Add Current Page", self)
        add_action.triggered.connect(self.add_current_page_bookmark)
        self.bookmarks_menu.addAction(add_action)

        clear_action = QAction("Clear All Bookmarks", self)
        clear_action.triggered.connect(self.clear_bookmarks)
        self.bookmarks_menu.addAction(clear_action)
        self.bookmarks_menu.addSeparator()

        for item in self.bookmarks[:12]:
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            if not url:
                continue
            action = QAction(title, self)
            action.triggered.connect(lambda _checked=False, u=url: self.add_browser_tab(QUrl(u), switch_to=True))
            self.bookmarks_menu.addAction(action)
            self.bookmarks_toolbar.addAction(action)

    def clear_bookmarks(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Clear Bookmarks",
                "Delete all bookmarks?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.bookmarks = []
        self.state.save_bookmarks(self.bookmarks)
        self.refresh_bookmarks_ui()

    def record_history_from_view(self, view: BrowserView, ok: bool) -> None:
        if not ok:
            return
        url = view.url().toString()
        title = view.title() or url
        if not url:
            return
        if self.history and self.history[-1].get("url") == url:
            self.history[-1]["title"] = title
        else:
            self.history.append({"title": title, "url": url})
        self.state.save_history(self.history)
        self.settings_store["last_url"] = url
        self.state.save_settings(self.settings_store)

    def show_history(self) -> None:
        dialog = HistoryDialog(self)
        dialog.exec()

    def clear_history(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Clear History",
                "Delete your saved history entries?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.history = []
        self.state.save_history(self.history)
        self.statusBar().showMessage("History cleared", 2000)

    def closeEvent(self, event: QCloseEvent) -> None:
        view = self.current_view()
        if view is not None:
            self.settings_store["last_url"] = view.url().toString()
            self.state.save_settings(self.settings_store)
        self.state.save_history(self.history)
        self.state.save_bookmarks(self.bookmarks)
        super().closeEvent(event)


def main() -> int:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "0")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    window = BrowserMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
