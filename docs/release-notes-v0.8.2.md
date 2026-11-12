# Heating Intelligence v0.8.2 — Automatic card resource versioning

## Highlights

- Automatically register the Lovelace card resource in storage-managed Lovelace setups.
- Automatically update the existing resource URL to include the installed integration version.
- Preserve the resource entry ID and avoid duplicate card resources.
- Remove the need to manually change the resource query string after each integration update.
- Keep YAML-managed Lovelace resource configuration under user control, with documented manual versioning.

## Compatibility
- No changes to room configuration, sensors, prediction calculations, or read-only behaviour.
- The resource is automatically managed only when Lovelace resources use storage mode (the Home Assistant UI default).
- YAML resource mode cannot be modified by integrations; users must update the URL version in YAML.

## Cache behaviour
Each integration version produces a distinct resource URL, such as `/heating_intelligence/heating-intelligence-card.js?v=0.8.2`. Browsers and the Home Assistant Companion app therefore request the new JavaScript after the integration updates the stored resource URL.
