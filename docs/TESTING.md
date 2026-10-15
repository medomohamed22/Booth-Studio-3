# Verification notes

## Environment

- Blender 5.1.2 on Windows.
- Add-on metadata declares Blender 5.0 as the minimum; other versions and platforms were not validated.

## Recorded checks

The 3.0 development cycle recorded 53 automated checks covering registration, search, asset organization, downloads, failure handling, live mapping, resolution replacement, lighting profiles, pins, Solo, light linking, cache behavior, and light-map event logic.

The 3.0.1 fix recorded 12 additional isolated checks covering page-scoped picker entries, native selection indices, navigation bounds, partial pages, column changes, loading placeholders and empty catalogs.

Live checks verified actual provider search/downloads, real 2K texture dimensions, nonzero model preview icons, metadata responses, the gallery UI, and matching scene fingerprints before and after installation. The visual light map was rendered with Blender's GPU offscreen API; modal logic used synthetic event records rather than a human mouse gesture.

## Release-package verification

For this distribution, the six Python source files were checked against the current 3.0.1 source. ZIP integrity, package layout, Python parsing and isolated Blender registration/unregistration were checked.

## Boundaries

- The large browser is a native dialog with configurable opening width, not a separate Asset Browser editor.
- Progress represents the current file, not aggregate multi-file progress.
- Only provider-supported resolutions can be downloaded.
- Correct UV normal mapping requires valid UVs. Box mapping uses height-based bump.
- Provider availability and metadata can vary. First-time previews require internet.
- These recorded results do not establish compatibility with every GPU, operating system, Blender version or third-party add-on.
