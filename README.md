# Lumencrest - Fisch Macro

A Windows desktop macro for **Fisch on Roblox**, featuring visual SHAKE detection, automatic reeling, and a clean, hotkey-driven interface. 



## Warning!!
**This macro DOES NOT attach to roblox and or modify roblox in anyway, it simply a program that clicks like a human but faster and fully automatic, WE'RE NOT RESPONSABLE FOR BANS!! USE AT YOUR OWN RISK OF GETTING BANNED**



## Support<3

[![Support on Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20Development-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/lumynith)


##
> **Status:** In development. Features and compatibility may change between releases.

## Features

- **Automatic casting** with adjustable hold duration.
- **SHAKE detection** using OpenCV image recognition.
- **Automatic reeling** that tracks the fish indicator and control bar.
- **Calibration** for screen regions and visual references.
- **Global hotkeys** that work while Roblox is focused.
- **Auto-hide UI** while the macro runs, restoring it when paused or stopped.
- **Session statistics and debugging tools** to help monitor performance.

Some rods, colors, screen resolutions, and game updates may require additional calibration or may not yet be supported.

## Download and install

**You do not need to install Python or download the source code to use the Windows release.**

1. Go to the **[Releases](../../releases)** page.
2. Download the latest Windows `.zip`.
3. extract the `.zip` before running the program.
4. Launch the executable and open **Fisch** in Roblox.
5. Configure calibration if needed, then use the hotkeys below.

> If no downloadable release appears yet, a public build has not been published. Use the release notes for version-specific setup instructions.

## Hotkeys

| Key | Action |
| --- | --- |
| `F6` | Start / Pause / Resume |
| `F8` | Stop the macro |

The interface automatically hides while the macro is running and returns when the macro is paused or stopped.

## How it works

1. **Cast:** Holds the mouse button for a configurable duration.
2. **Shake:** Finds and clicks SHAKE prompts using image recognition.
3. **Reel:** Tracks the fish and control bar to control the reeling minigame.
4. **Repeat:** Continues the fishing workflow when the next attempt is ready.

## Troubleshooting

- **Macro doesn't detect Roblox:** Make sure Roblox is open, visible, and not minimized.
- **SHAKE prompts aren't detected:** Recalibrate the SHAKE region and reference image.
- **Reeling behaves incorrectly:** Check the REEL calibration and controller settings; some rod appearances may not be supported yet.
- **Hotkeys aren't working:** Check for conflicting hotkeys in other applications.

When reporting an issue, include your app version, a description of the problem, and any relevant logs. Avoid sharing private information.


## Distribution and license

The **Releases** page is intended for downloadable Windows builds. The executable is not source code, and a public `.exe` release does not by itself grant permission to modify or redistribute the software. Refer to any license included with the repository or release for the applicable terms.
