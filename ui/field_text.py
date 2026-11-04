"""Labels and one-line explanations for the settings forms, keyed by (config section, field)."""

FIELDS = {
    ("cast", "hold_seconds"): ("Cast hold", "Seconds the mouse is held to cast."),
    ("cast", "recast_delay"): ("Recast delay", "Seconds after a fish is caught or lost before casting again."),
    ("cast", "recast_timeout"): ("Recast if nothing happens", "Cast again if no reel minigame appears this many "
                                 "seconds after a cast. 0 = never."),

    ("shake", "threshold"): ("Shake match threshold", "Minimum score to treat a match as a SHAKE button."),
    ("shake", "click_delay"): ("Delay between clicks", "Minimum seconds between two shake clicks."),
    ("shake", "auto_click"): ("Click shake buttons", "Off: shake buttons are only highlighted (debug)."),
    ("shake", "first_button_timeout"): ("First button timeout", "Seconds to wait for the first button after a cast."),
    ("shake", "finish_after"): ("Done after", "Shaking counts as finished after this many seconds with no button."),
    ("shake", "move_settle"): ("Hover before click", "Seconds between moving the cursor and pressing."),
    ("shake", "click_hold"): ("Click length", "Seconds each click is held down."),

    ("reel_control", "kp"): ("Kp: chase strength", "How hard the bar is pushed toward the fish."),
    ("reel_control", "kd"): ("Kd: braking", "Damps relative speed so the bar doesn't overshoot the fish."),
    ("reel_control", "deadzone"): ("Deadzone", "Position errors smaller than this (track widths) are ignored."),
    ("reel_control", "hysteresis"): ("Hysteresis", "Output must pass ± this to switch between hold and release."),
    ("reel_control", "prediction_time"): ("Look-ahead", "Seconds of fish and bar motion to predict."),
    ("reel_control", "min_input_interval"): ("Min switch interval", "Minimum seconds between hold / release switches."),
    ("reel_control", "sticky_distance"): ("Sticky distance", "This close to the fish, spam click so the bar hovers."),
    ("reel_control", "sticky_interval"): ("Spam click speed", "Seconds per press and per release while sticky."),
    ("reel_control", "lost_grace"): ("Tracking dropout grace", "Keep the last output through dropouts this short."),
    ("reel_control", "brake_decel"): ("Brake strength", "How fast the bar slows when pushed back. Lower brakes "
                                     "earlier (less overshoot); 0 turns braking off."),
    ("reel_control", "hold_moves_right"): ("Holding moves the bar right", "Set by hold-direction calibration."),

    ("reel", "active_threshold"): ("Minigame visible score", "Score that counts as the reel minigame being on screen."),
    ("reel", "inactive_threshold"): ("Minigame gone score", "Score that counts as the minigame having closed."),
    ("reel", "confirm_frames"): ("Frames to confirm visible", "Consecutive frames before the minigame counts as up."),
    ("reel", "inactive_confirm_frames"): ("Frames to confirm gone", "Consecutive frames before it counts as closed."),

    ("tracking", "fish_threshold"): ("Fish confidence", "Minimum confidence to trust the fish position."),
    ("tracking", "bar_threshold"): ("Bar confidence", "Minimum confidence to trust the control bar position."),
    ("tracking", "max_jump"): ("Max jump", "Bigger one-frame moves (track widths) need a second frame to agree."),

    ("catch", "threshold"): ("Catch indicator threshold", "Minimum score for the success / failure images."),
    ("catch", "confirm_frames"): ("Frames to confirm", "Consecutive frames that must agree on the result."),
    ("catch", "verify_timeout"): ("Verify timeout", "Seconds to wait for an indicator before UNKNOWN."),

    ("app", "auto_hide"): ("Hide window while running",
                           "F6 hides this window and focuses Roblox; pausing or stopping brings it back."),
}


def describe(section: str, name: str) -> tuple[str, str]:
    return FIELDS.get((section, name), (name.replace("_", " ").capitalize(), ""))
