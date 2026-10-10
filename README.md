# Send to LightBurn (Inkscape extension)

An Inkscape 1.4 extension with two commands, both under **Extensions > LightBurn**:

- **Send to LightBurn** sends the current drawing straight into LightBurn.
- **Save SVG** saves a LightBurn-friendly SVG copy of the drawing.

Both work on a temporary copy, so your open document is never changed.

## Requirements

- Inkscape 1.4.x (developed and tested on 1.4.2)
- LightBurn on the same computer. LightBurn 2.2 or newer can use its REST API. Older versions work through the UDP fallback.
- Windows or macOS. Linux should work for Save SVG, but sending has only been tried on Windows and macOS.

## Install

1. Download the [Inkscape LightBurn Extensions ZIP](https://github.com/nicholas-a11y/Inkscape/raw/refs/heads/main/Inkscape-LightBurn-Extensions.zip) and extract it. It contains the two extension dialogs and their shared Python file.
2. In Inkscape, open **Edit > Preferences > System** on Windows, or **Inkscape > Settings > Preferences > System** on macOS.
3. Next to **User extensions**, click **Open**. This opens the right folder for your computer.
4. Copy the three extracted files into that folder:
   - `lightburn_export.inx`
   - `lightburn_save.inx`
   - `lightburn_export.py`
5. Restart Inkscape. The commands appear under **Extensions > LightBurn**.

## LightBurn colour palette (optional)

The [LightBurn Layers Palette ZIP](https://github.com/nicholas-a11y/Inkscape/raw/refs/heads/main/LightBurn-Layers-Palette.zip) is optional. It contains `LightBurn_Layers.gpl`, an Inkscape palette with LightBurn's 30 layer colours (C00 to C29) plus the two tool layers (T1 and T2). Using these exact colours helps your strokes land on the layer you expect when the file is imported.

To add it:

1. Download and extract the ZIP.
2. In Inkscape, open **Edit > Preferences > System** on Windows, or **Inkscape > Settings > Preferences > System** on macOS.
3. Next to **User palettes**, click **Open**.
4. Copy `LightBurn_Layers.gpl` into that folder.
5. Restart Inkscape.
6. Open the palette list at the right-hand end of the palette bar along the bottom of the window (or in the Swatches dialog) and choose **LightBurn Layers**.

Click a colour to set the **fill**. Hold **Shift** and left-click a colour to set the **stroke**, which is the colour LightBurn normally uses to pick the layer for line work.

## Using it

### Send to LightBurn

Choose **Extensions > LightBurn > Send to LightBurn**, set your options and click **Send**.

| Option | What it does |
| --- | --- |
| Selected objects only | Sends just what you have selected. |
| Convert text (and shapes) to paths | Runs Object to Path on everything first. Text then looks the same in LightBurn whatever fonts are installed. |
| Ungroup everything | Ungroups all groups before sending. |
| Connection method | **Auto** tries the REST API first, then UDP. **UDP only** is for LightBurn older than 2.2. |
| If LightBurn isn't running, start | Starts either **LightBurn** or **LightBurn (PreRelease)**. |
| When opening with REST API | **Merge** into the current project or **Replace** it. |

### Save SVG

Choose **Extensions > LightBurn > Save SVG**. It has the same first three options, plus a file name. If you leave the name blank, the file is saved next to your drawing with `_lightburn` added to the name. A message shows where it was saved.

## How sending works

1. **REST API (LightBurn 2.2+).** The extension talks to `127.0.0.1:19520`. The first time, LightBurn shows a permission prompt to pair with "Inkscape (Send to LightBurn)". You have about 30 seconds to accept. The access token is stored in a per-user config folder, not in the drawing. If it stops working, the extension pairs again once. Only the upload permission is requested.
2. **UDP fallback.** If the REST API isn't available, it sends a `LOADFILE` command to LightBurn on UDP port 19840. This replaces the open project, and LightBurn may ask you to save first.
3. **Starting LightBurn.** If nothing answers, the extension starts LightBurn (or the PreRelease build, if you picked it) and loads the file. Only the standard `LightBurn` and `LightBurn_PreRelease` install folders are used.

The REST API reports that a file was received, not that LightBurn imported it. If a drawing doesn't show up, check LightBurn for an import message.

## Debug log

Hold **Alt** (Windows) or **Option** (macOS) while clicking Send to see a step-by-step log with timings. A copy is always written to `%TEMP%\inkscape_to_lightburn\last_run.log` (or the temp folder on macOS).

## Things to know

- **Sizes:** LightBurn measures the path centre line, but Inkscape can show sizes including the stroke width. Open **Edit > Preferences > Tools** on Windows, or **Inkscape > Settings > Preferences > Tools** on macOS, and set **Bounding box to use** to **Geometric** so the sizes you see match what LightBurn imports.
- **Colours and layers:** LightBurn picks the layer from the stroke colour on import, so use its palette colours exactly. The extension cannot change which layer an object lands on. The REST API doesn't allow editing layer colours or the T1 and T2 tool layers.
- **Clipping:** clipped objects can still show their hidden parts in LightBurn. Flatten clips in Inkscape first, for example with Path > Intersection.

## Files

| File | Purpose |
| --- | --- |
| `lightburn_export.inx` | Dialog for **Send to LightBurn** |
| `lightburn_save.inx` | Dialog for **Save SVG** |
| `lightburn_export.py` | The extension code, used by both |
| `LightBurn_Layers.gpl` | Optional Inkscape palette of the LightBurn layer colours |
