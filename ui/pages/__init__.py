"""The main window's pages. Each gets the shared AppContext from ui.main_window."""

from core.macro_engine import MacroState
from core.state_machine import Phase
from core.window_manager import WindowState
from ui import theme

STATUS_COLORS = {"Idle": theme.TEXT_FAINT, "Running": theme.GREEN, "Paused": theme.AMBER, "Stopped": theme.RED}
STAGES = {Phase.CASTING: "Casting", Phase.SHAKING: "Shaking", Phase.REELING: "Reeling",
          Phase.VERIFYING: "Checking catch"}
ROBLOX = {WindowState.CLOSED: ("Not found", theme.RED), WindowState.MINIMIZED: ("Minimized", theme.AMBER),
          WindowState.OPEN: ("In background", theme.AMBER), WindowState.ACTIVE: ("Focused", theme.GREEN)}


def stage(ctx) -> str:
    return "Not fishing" if ctx.runtime.engine.state is MacroState.STOPPED else STAGES[ctx.runtime.cycle.phase]


def duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def success_rate(counts) -> str:
    from detection.catch_detector import CatchResult
    caught, failed = counts[CatchResult.CATCH_SUCCESS], counts[CatchResult.CATCH_FAILED]
    return f"{caught / (caught + failed):.0%}" if caught + failed else "—"
