"""Panel layout mode helpers.

Two layout modes exist:
  * "classic": top, bottom, left and right panels (default)
  * "single":  one exclusive bottom panel hosting every widget
"""

PANEL_MODE_KEY = ["org.jfshell.panel", "mode"]
MODE_CLASSIC = "classic"
MODE_SINGLE = "single"


def get_panel_mode(config_handler) -> str:
    try:
        value = config_handler.get_root_setting(PANEL_MODE_KEY, MODE_CLASSIC)
    except Exception:
        return MODE_CLASSIC
    return MODE_SINGLE if str(value).strip().lower() == MODE_SINGLE else MODE_CLASSIC


def is_single_mode(source) -> bool:
    """Accepts a panel instance (with config_handler) or a config handler."""
    handler = getattr(source, "config_handler", source)
    return get_panel_mode(handler) == MODE_SINGLE


# Vertical space the single panel takes around its content (px).
# Keep in sync with .single-panel-full padding (2+2) and the 1px top border.
SINGLE_PANEL_CHROME = 5
# Margin above and below each button (px).
SINGLE_BUTTON_MARGIN = 2
# Border + padding inside a button, vertical (px).
SINGLE_BUTTON_CHROME = 8
SINGLE_DEFAULT_HEIGHT = 40
SINGLE_MIN_BUTTON_HEIGHT = 20
SINGLE_MIN_ICON = 12
# Auto icon size as a share of the button height.
SINGLE_ICON_RATIO = 0.6


def _as_int(value, fallback):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def single_panel_metrics(config_handler, icon_override: int = 0) -> dict:
    """Sizes shared by every widget of the single panel, derived from its height."""
    height = max(
        _as_int(
            config_handler.get_root_setting(
                ["org.jfshell.panel", "single", "height"], SINGLE_DEFAULT_HEIGHT
            ),
            SINGLE_DEFAULT_HEIGHT,
        ),
        1,
    )
    button_height = max(
        height - SINGLE_PANEL_CHROME - 2 * SINGLE_BUTTON_MARGIN,
        SINGLE_MIN_BUTTON_HEIGHT,
    )
    # Auto size is a fraction of the button, so icons keep breathing room.
    ceiling = max(button_height - SINGLE_BUTTON_CHROME, SINGLE_MIN_ICON)
    icon = max(round(button_height * SINGLE_ICON_RATIO), SINGLE_MIN_ICON)
    if icon_override > 0:
        icon = icon_override
    icon = max(min(icon, ceiling), SINGLE_MIN_ICON)
    return {
        "panel_height": height,
        "button_height": button_height,
        "icon_size": icon,
        "button_margin": SINGLE_BUTTON_MARGIN,
    }
