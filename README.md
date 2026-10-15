# Booth Studio

**PBR materials, free asset libraries, HDRI environments, and studio lighting for Blender.**

Booth Studio brings common material and lighting tasks into one sidebar. Browse photographed textures, models, and HDRIs; adjust material mapping; and build lighting rigs for exhibition booths, products, jewelry, and cars.

**Version:** 3.0.1  
**Tested environment:** Blender 5.1.2 on Windows  
**Location:** 3D Viewport → Sidebar → Booth Studio

No external Python packages, MCP connection, or AI service is required to use the add-on. Online asset browsing uses public provider endpoints without an API key. Offline procedural materials and previously downloaded assets can be used without internet access.

## Download and installation

1. Download **`booth_studio-3.0.1.zip`** from the repository's Releases section, or use the copy in [`dist/`](dist/).
2. In Blender, open **Edit → Preferences → Add-ons**.
3. Open the menu in the top-right corner and choose **Install from Disk…**. The exact menu location can vary between Blender versions.
4. Select `booth_studio-3.0.1.zip` without extracting it.
5. Enable **Booth Studio - Smart Lighting & Materials** if it is not already enabled.
6. In the 3D Viewport, press **N** and open the **Booth Studio** tab.

Install the release ZIP, not GitHub's automatically generated source ZIP. Disable older Booth Studio copies before updating; do not enable multiple versions together. Restart Blender after replacing an older installation if stale panels remain.

The add-on declares Blender 5.0 as its minimum version. Verification was performed on Blender 5.1.2/Windows; other Blender versions and operating systems have not been validated.

## Features

| Area | Features |
| --- | --- |
| Asset browser | Large native Blender dialog, adjustable opening width, image size and columns, paginated previews, direct image selection |
| Libraries | Poly Haven materials, models and HDRIs; ambientCG materials; combined material search |
| Search | Category filters, HDRI indoor/outdoor filters, name/popularity sorting, selected Arabic keyword translations |
| Organization | Favorites, named collections, recent imports, and discovery of downloaded assets |
| Previews | Enlarged images, available resolutions and download sizes, source dimensions/polycounts when supplied |
| Downloads | Background transfers, serial import queue, current-file progress, cancel/retry, explicit application of ready downloads |
| Performance | Visible-page preview loading, next-page disk prefetch, bounded preview memory, optional thumbnail cleanup |
| Materials | PBR folder import, UV or box mapping, centimeter tile size, XYZ scale/offset/rotation, quick grain angles, optional live updates |
| Resolution changes | Replace supported library material maps with 1K/2K/4K versions while retaining mapping and shader adjustments |
| Lighting | Six rigs, individual light controls, pins, Solo, native light linking, interactive light map, profiles and A/B comparisons |

## Quick start: import an asset

1. Open **Library → Open Large Asset Browser**.
2. Choose **Poly Haven**, **ambientCG**, or **All Libraries**, and select an asset type.
3. Enter a search term such as `wood`, `chair`, or `studio`, then click **Search**.
4. Browse with the page arrows. Click a card's name or use the native image picker above the gallery to select an asset.
5. Use **Enlarge Preview** for a larger image. **Inspect Resolutions / Download Size** fetches additional download information.
6. Select a resolution and click **Download / Import**.

For materials, select the destination mesh objects before importing. Models are added to a separate collection. HDRIs create a separate World datablock.

If the scene or selection changes while an asset downloads, Booth Studio keeps the files and marks the job as ready. Use **Apply to Current Selection** when you are ready to apply it.

**3.0.1 preview fix:** the native image picker now displays the current page only, matching the loaded thumbnails. Use the page arrows to browse the rest of the results. Close and reopen an image picker left open during an update.

## Materials and wood direction

Select your objects and open **Material**:

- Choose an axis, then use **0° / 90° / 180°** for quick grain rotation.
- Set **Tile Width (cm)** for the texture repeat size.
- Enable **Advanced Controls** for coordinates, XYZ rotation, stretch and offset.
- Click **Apply Mapping to Selected** to apply the current settings.
- Optionally enable **Live Material Preview** for debounced updates while adjusting controls.

Live updates are off by default. Enable them only when the panel values are the values you want applied to the selected materials.

**Object/Box** mapping works without a manual UV unwrap and uses height-based bump. **UV** mode enables tangent-space OpenGL normal maps and requires valid UVs. DirectX-only normal maps are not supported by the local importer. Texture scale is not automatically inferred for every asset; set the tile width to suit the source material. Reapply mapping after applying object Scale if the texture size changes.

To change an imported library material's resolution, select it, choose **1K**, **2K**, or **4K**, and click **Replace Map Resolution**. Availability depends on the source asset. This operation replaces image maps rather than rebuilding the shader.

Local folders can provide color/albedo, roughness, metallic, NormalGL, height/displacement and AO maps. The ten built-in procedural materials are offline alternatives, including oak/walnut-style materials; use the online libraries for photographed wood textures.

## Studio lighting

Select the booth or product geometry, open **Lighting**, and choose a style:

| Style | Intended use |
| --- | --- |
| Booth — Exhibition | Softboxes and a ceiling spotlight grid |
| Booth — Warm Evening | Warm exhibition lighting |
| Product — Soft Studio | Broad, soft product illumination |
| Jewelry — Contrast | Small sources and strip highlights |
| Car — Strip Studio | Long reflection strips and overhead light |
| Car — Dramatic | Reduced fill and stronger rim illumination |

Click **Generate / Update**. Use intensity, rig rotation and temperature to adjust the setup. Avoid including an oversized studio floor in the selected bounds; alternatively, enter manual dimensions.

Choose a **Studio Light** to adjust its power and color, toggle it, isolate it with **Solo**, or **Pin** it. Regenerating recalculates unpinned managed lights. Pinned lights retain their adjustments. Lights outside Booth Studio's managed rig are not removed by its generation or cleanup tools.

With Advanced Controls enabled, **Affect Selected Meshes Only** assigns a native light-linking receiver collection. **Affect All Objects** clears that restriction. Render-engine support follows Blender's native light-linking behavior.

### Interactive light map

Run **Drag Lights on Visual Map** from a 3D Viewport:

- Click and drag a numbered point to move a light horizontally.
- Use the mouse wheel to change the selected light's height.
- Press **Enter** to apply or **Esc** to restore the original transforms.

### Profiles and A/B comparisons

Save a named profile to reuse a rig. Profiles include managed lights, environment settings and exposure. Advanced Controls exposes JSON import/export.

Use **Save A / Save B** and **View A / View B** to compare two snapshots. Loading A/B restores the recorded snapshot, including recorded pin states. Normal profile loading respects currently pinned lights. Scene-specific light-linking receiver assignments are not included in exported profiles.

## Downloads and local data

Configure **Asset Cache** under Advanced Controls. Favorites, collections, recent imports and profiles are stored in `studio_library.json` in that folder. Downloaded assets and thumbnails also stay in the cache. A/B snapshots and scene settings are stored with your `.blend` when you save it.

- Progress and transfer size refer to the **current file**, not the total of a multi-file asset.
- Cancellation is checked during network reads; a pending connection may take up to the network timeout to return.
- Interrupted jobs become retryable after loading a file or restarting Blender; they are not silently resumed.
- **Cache Size** reports local usage. **Clear Photos** removes thumbnail files while retaining downloaded assets and organization data.
- Poly Haven downloads are checked against published MD5 values when provided. ambientCG cached extracted files use locally recorded SHA256 hashes.

Network requests are made to the selected asset providers and their download/CDN hosts. No API keys are requested or stored. No paid service integration is included. First-time searches, previews and downloads need internet access; provider endpoints and availability can change.

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| Missing preview images | Wait for the current page's photos, reopen the image picker, or run Search again. Use arrows to change pages. |
| No model results | Choose Poly Haven and Models; clear material-only category filters and try a model keyword. |
| Import remains ready | Restore the intended selection, then use Apply to Current Selection. |
| Incorrect texture direction/size | Check the chosen coordinate mode and UVs, adjust the axis/tile width, then reapply mapping. |
| Unsupported resolution | Inspect the available resolutions and choose a supported one. |
| Unexpected light placement | Select only the subject geometry or use manual bounds. |
| Old or duplicate panels | Disable older copies of Booth Studio and restart Blender. |

## Asset sources and credits

Booth Studio is an independent add-on and is not an official product of either provider. Downloaded assets are supplied by their respective creators; the add-on ZIP does not bundle their libraries.

- **[Poly Haven](https://polyhaven.com/)** — models, HDRIs and textures. See the [public API information](https://polyhaven.com/our-api) and [asset license](https://polyhaven.com/license).
- **[ambientCG](https://ambientcg.com/)** — PBR materials. See the [license documentation](https://docs.ambientcg.com/license/).

Both providers publish their assets under CC0. Their asset licenses are separate from any software license chosen for this repository. Please consider supporting the original asset creators.

## Validation and feedback

Development validation recorded 53 checks for the 3.0 feature set and 12 regression checks for the 3.0.1 image-picker fix. These are historical verification results, not a claim that every feature has been tested on every machine. See [docs/TESTING.md](docs/TESTING.md) for the verification boundaries.

When reporting an issue, include your Blender version, operating system, selected provider/type, reproduction steps, and the error or job status. Screenshots are helpful; remove private project details before sharing them.

See [CHANGELOG.md](CHANGELOG.md) for release changes.
