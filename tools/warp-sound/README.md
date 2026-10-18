# Private sampled-sound bundle builder

`build_private.py` creates a sample-enabled VESC app image and a staged music-service catalogue from private inputs. The generic `app.lisp` stays buildable without the sample and does not reserve song 7; only the generated private app injects sample imports, playback code and song-7 dispatch.

Run the builder with the sample stream, its expected SHA256, a resource-backup directory containing `installed-imports.json` and the three preserved package resources, an existing base `songs.json`, and an output directory outside this repository. The output contains the exact derived WAV preview, signed sample chunks, generated app source and image, staged catalogue, and trigger extension files. It never writes to a vehicle.

The builder validates the sample length and caller-supplied digest, checks every preserved package resource against the backup metadata, verifies the generated image's import bytes including the importer-added NUL, and enforces image capacity. Keep the sample, digest invocation, backup resources and generated bundle private. Validate the target app and installed music-service revision separately before installation; this tool does not establish runtime compatibility or playback safety.
