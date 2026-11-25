# v0.5.1: usable osu! onset reference exports

- Export circles at the osu!standard playfield center `256,192` rather than alternating between two columns.
- Set `[General] StackLeniency: 0`.
- For MP3 sources, set `[General] AudioFilename: audio.mp3` and safely copy original MP3 bytes into the export directory when needed. Refuse to replace mismatching existing audio.mp3. WAV/OGG retain their actual formats.
- Export into a separate existing folder, with a companion recording beside the new difficulty. Exclusive creation protects originals.
- Density presets: lightweight (80ms default), moderate (40ms), full fidelity (0ms). Density reduction affects export alone. AR9 for lightweight modes; full AR5 unchanged.
- Corrected output counts and added detailed regression tests for audio identity, no-overwrite, files in different folders, 0 stack leniency, centered positions and density reduction.
- All signal processing, metadata interpretation, candidate generation, Current, graph scheduler, playback and import logic unchanged.
- Local Qt tests skipped: use Windows QA for production validation and investigate real osu! gameplay performance.
