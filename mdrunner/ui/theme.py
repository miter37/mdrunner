"""Visual design system for the mdrunner desktop app.

Direction: an operator's console — the calm, precise feel of ``systemctl
list-units`` or a CI dashboard. Slate neutrals, one restrained indigo
accent, and a semantic status palette that does the real communicating.
A monospace "data spine" runs through every value-bearing column.

One Fusion base + a QSS stylesheet, in light and dark. No third-party deps.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# --- type -------------------------------------------------------------------

# The instrument of this app's world: CLIs, cron specs, timestamps, tokens.
MONO_FAMILIES = [
    "JetBrains Mono", "SF Mono", "Cascadia Code", "Menlo",
    "DejaVu Sans Mono", "Consolas", "monospace",
]

# --- palettes -------------------------------------------------------------------

LIGHT = {
    "bg": "#FBFBFD",
    "surface": "#FFFFFF",
    "surface_alt": "#F3F4F7",
    "border": "#E2E4EA",
    "border_strong": "#CDD1DA",
    "text": "#1A1D24",
    "text_dim": "#697184",
    "text_faint": "#98A0AF",
    "accent": "#3A5CCC",
    "accent_soft": "#E7ECFb",
    "sel": "#E7ECFb",
    "ok": "#1F9D63",
    "warn": "#B87608",
    "err": "#CE4747",
    "running": "#3A5CCC",
    "idle": "#98A0AF",
}

DARK = {
    "bg": "#0F1115",
    "surface": "#1B1F27",
    "surface_alt": "#252A34",
    "border": "#333A45",
    "border_strong": "#454D5B",
    "text": "#E7E9EE",
    "text_dim": "#99A1B0",
    "text_faint": "#6B7280",
    "accent": "#7E96EC",
    "accent_soft": "#242B3D",
    "sel": "#2A3350",
    "ok": "#43C489",
    "warn": "#E0A233",
    "err": "#E86D6D",
    "running": "#7E96EC",
    "idle": "#6B7280",
}

PALETTES = {"light": LIGHT, "dark": DARK}
_SETTINGS_KEY = "ui/theme"


def mono_font(size: int = 12) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(size)
    return f


def style_form(form) -> None:
    """Consistent spacing/alignment for every QFormLayout in the app."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QFormLayout as _QF

    form.setContentsMargins(4, 4, 4, 4)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(9)
    form.setLabelAlignment(_Qt.AlignmentFlag.AlignRight | _Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(_QF.FieldGrowthPolicy.ExpandingFieldsGrow)


def status_color(p: dict, kind: str) -> str:
    return p.get(kind, p["idle"])


def resolve_mode(mode: str) -> str:
    """'light' | 'dark' | 'system' -> a concrete 'light'/'dark'."""
    if mode in ("light", "dark"):
        return mode
    app = QApplication.instance()
    if app is not None:
        hint = app.palette().color(QPalette.ColorRole.Window)
        if hint.lightnessF() < 0.5:
            return "dark"
    return "light"


def saved_mode(default: str = "system") -> str:
    try:
        return QSettings().value(_SETTINGS_KEY, default) or default
    except Exception:  # noqa: BLE001
        return default


def set_saved_mode(mode: str) -> None:
    try:
        QSettings().setValue(_SETTINGS_KEY, mode)
    except Exception:  # noqa: BLE001
        pass


def _qpalette(p: dict) -> QPalette:
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.ColorRole.Window, c(p["bg"]))
    pal.setColor(QPalette.ColorRole.Base, c(p["surface"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, c(p["surface_alt"]))
    pal.setColor(QPalette.ColorRole.Text, c(p["text"]))
    pal.setColor(QPalette.ColorRole.WindowText, c(p["text"]))
    pal.setColor(QPalette.ColorRole.ButtonText, c(p["text"]))
    pal.setColor(QPalette.ColorRole.Button, c(p["surface"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, c(p["surface"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, c(p["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, c(p["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c(p["text_faint"]))
    pal.setColor(QPalette.ColorRole.Link, c(p["accent"]))
    dis = QPalette.ColorGroup.Disabled
    pal.setColor(dis, QPalette.ColorRole.Text, c(p["text_faint"]))
    pal.setColor(dis, QPalette.ColorRole.WindowText, c(p["text_faint"]))
    pal.setColor(dis, QPalette.ColorRole.ButtonText, c(p["text_faint"]))
    return pal


def build_qss(p: dict) -> str:
    return f"""
* {{ outline: 0; }}
QWidget {{
    background: {p['bg']};
    color: {p['text']};
    font-size: 13px;
}}
QLabel {{ background: transparent; }}
QMainWindow::separator {{ background: {p['border']}; width: 1px; height: 1px; }}
QToolTip {{
    background: {p['surface']}; color: {p['text']};
    border: 1px solid {p['border_strong']}; padding: 4px 7px; border-radius: 5px;
}}

/* --- Toolbar --- */
QToolBar {{
    background: {p['surface_alt']};
    border: 0; border-bottom: 1px solid {p['border']};
    padding: 6px 8px; spacing: 4px;
}}
QToolBar QToolButton {{
    background: transparent; color: {p['text_dim']};
    border: 1px solid transparent; border-radius: 6px;
    padding: 5px 9px; font-weight: 500;
}}
QToolBar QToolButton:hover {{ background: {p['surface']}; color: {p['text']};
    border-color: {p['border']}; }}
QToolBar QToolButton:pressed {{ background: {p['sel']}; }}
QToolBar QToolButton:checked {{ background: {p['accent_soft']}; color: {p['accent']};
    border-color: {p['accent']}; }}
QToolBar::separator {{ background: {p['border']}; width: 1px; margin: 5px 6px; }}

/* --- Menu bar --- */
QMenuBar {{ background: {p['surface_alt']}; border-bottom: 1px solid {p['border']};
    padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {p['sel']}; color: {p['text']}; }}
QMenu {{ background: {p['surface']}; border: 1px solid {p['border_strong']};
    border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p['accent']}; color: #FFFFFF; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 8px; }}

/* --- Tables --- */
QTableWidget, QTableView {{
    background: {p['surface']};
    alternate-background-color: {p['surface_alt']};
    border: 1px solid {p['border']}; border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {p['sel']};
    selection-color: {p['text']};
}}
QTableView::item {{ padding: 4px 10px; border: 0;
    border-bottom: 1px solid {p['border']}; }}
QTableView::item:selected {{ background: {p['sel']}; color: {p['text']}; }}
QHeaderView {{ background: {p['surface_alt']}; }}
QHeaderView::section {{
    background: {p['surface_alt']}; color: {p['text_dim']};
    padding: 7px 10px; border: 0; border-bottom: 1px solid {p['border_strong']};
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.6px;
}}
QHeaderView::section:first {{ border-top-left-radius: 8px; }}
QHeaderView::section:last {{ border-top-right-radius: 8px; }}
QTableCornerButton::section {{ background: {p['surface_alt']}; border: 0; }}

/* --- Inputs --- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {p['surface']}; color: {p['text']};
    border: 1px solid {p['border_strong']}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
}}
QPlainTextEdit, QTextEdit {{ padding: 6px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QComboBox:focus {{ border-color: {p['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {p['surface_alt']}; color: {p['text_faint']};
}}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {p['text_dim']};
    margin-right: 8px; }}
QComboBox QAbstractItemView {{
    background: {p['surface']}; border: 1px solid {p['border_strong']};
    border-radius: 6px; selection-background-color: {p['accent']};
    selection-color: #FFFFFF; padding: 3px;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: 0;
    background: {p['surface_alt']}; }}

/* --- Buttons --- */
QPushButton {{
    background: {p['surface']}; color: {p['text']};
    border: 1px solid {p['border_strong']}; border-radius: 6px;
    padding: 6px 14px; font-weight: 500;
}}
QPushButton:hover {{ background: {p['surface_alt']}; border-color: {p['text_faint']}; }}
QPushButton:pressed {{ background: {p['sel']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; background: {p['surface_alt']}; }}
QPushButton:default {{ background: {p['accent']}; color: #FFFFFF;
    border-color: {p['accent']}; }}
QPushButton:default:hover {{ background: {p['accent']}; }}
QDialogButtonBox QPushButton {{ min-width: 78px; }}

/* --- Group boxes / tabs --- */
QGroupBox {{
    border: 1px solid {p['border']}; border-radius: 8px;
    margin-top: 14px; padding: 12px; background: {p['surface']};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 5px; color: {p['text_dim']};
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.7px;
}}
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {p['text_dim']};
    padding: 7px 14px; border: 0; border-bottom: 2px solid transparent;
    margin-right: 2px; font-weight: 500;
}}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom-color: {p['accent']}; }}
QTabBar::tab:hover:!selected {{ color: {p['text']}; }}

/* --- Docks --- */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {p['surface_alt']}; padding: 7px 10px;
    border-bottom: 1px solid {p['border']};
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.7px; color: {p['text_dim']};
}}

/* --- Status bar --- */
QStatusBar {{ background: {p['surface_alt']}; border-top: 1px solid {p['border']}; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {p['text_dim']}; padding: 0 2px; }}

/* --- Checkboxes --- */
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {p['border_strong']}; background: {p['surface']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']};
    image: none; }}

/* --- Splitter / scrollbars --- */
QSplitter::handle {{ background: {p['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {p['border_strong']}; border-radius: 5px;
    min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {p['text_faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ background: {p['surface_alt']}; border: 0; border-radius: 4px;
    height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 4px; }}

QTableView#quotaTable::item {{ padding: 3px 7px; }}
QTableView#quotaTable {{ border: 0; background: {p['surface']}; }}
QListWidget#alertList {{
    background: {p['surface']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 8px;
    outline: 0; padding: 3px;
}}
QListWidget#alertList::item {{ padding: 4px 8px; border: 0; border-radius: 5px; }}
QListWidget#alertList::item:selected {{ background: {p['sel']}; color: {p['text']}; }}
QFrame#alertSep {{ color: {p['border']}; }}
QLabel#dim {{ color: {p['text_dim']}; }}
QLabel#logHeader {{ color: {p['text_dim']}; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.7px; padding: 6px 4px; }}
"""


def apply_theme(app: QApplication, mode: str) -> str:
    """Apply the resolved theme to ``app``. Returns the concrete mode used."""
    concrete = resolve_mode(mode)
    p = PALETTES[concrete]
    app.setStyle("Fusion")
    app.setPalette(_qpalette(p))
    app.setStyleSheet(build_qss(p))
    return concrete
