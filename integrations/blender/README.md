# Muffin Submitter — Blender

A Blender add-on that submits the current scene straight to your Muffin manager,
the same way Flamenco or Deadline integrate into the DCC.

## Install — one click (recommended for a new PC)
From the repo root, double-click **`install_addons.bat`** (or run
`python integrations/install_addons.py blender`). It drives Blender's own
installer headlessly to install **and enable** the add-on, finding `blender.exe`
via `MUFFIN_BLENDER_EXE` → Muffin's Settings (Blender path) → `PATH`. Set the
Blender path in **Muffin Node ▸ Settings** first if it isn't on `PATH`.

## Install — manually
1. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install…**
2. Pick `muffin_blender_submit.py`.
3. Enable **"Render: Muffin Submitter"**.
4. While still in Preferences, expand the add-on and set the **Manager URL**
   (e.g. `http://127.0.0.1:8080`). This is stored once per machine.

(Or drop the file into your Blender `scripts/addons/` folder.)

## Use
1. Open and **save** your `.blend` (workers need a real file path).
2. Set your frame range, output path, and render engine as usual.
3. Go to **Properties ▸ Output ▸ Muffin Submitter**.
4. The **Job name** auto-fills from the .blend file name (edit it to rename). Set
   **Frames / task** and **Priority**, then click **Muffin!!!!!!!!!**.
   (The Manager URL comes from the add-on preferences.)

The job appears immediately in Muffin's Monitor and starts rendering on any
available worker. The add-on uses only Blender's bundled Python (urllib), so
there's nothing extra to install.

## Notes
- `Output` is taken from Blender's render output path (`//...` is resolved to an
  absolute path). For a multi-machine farm this should be a shared/network path
  every worker can reach.
- The render engine (Cycles / EEVEE / …) is sent as the job's renderer.
