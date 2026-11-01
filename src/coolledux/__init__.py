"""Push GIFs and multi-slot playlists to CoolLEDUX LED panels (CoolLED1248 app)."""

__version__ = "0.1.0"

from . import limits
from .ble import Panel, PanelError, PanelInfo, find_panel, scan
from .playlist import (Frame, Playlist, PlaylistTooLong, export_gifs, fit_to_panel, load_gif,
                       load_many, plan, programs)

__all__ = ["limits", "Panel", "PanelError", "PanelInfo", "find_panel", "scan", "Frame", "Playlist",
           "PlaylistTooLong", "export_gifs", "fit_to_panel", "load_gif", "load_many", "plan", "programs"]
