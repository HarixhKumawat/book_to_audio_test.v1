"""storyaudio: EPUB/PDF/TXT to narrated MP3 audiobooks with Fish Audio Drama 3.

Layers (each layer calls only the one below it):

    cli.py ─────────────── studio/server.py         UI: command line, Casting Studio
       │                        │
    pipeline.py ──────────▶ engine/manager.py       services (pipeline autostarts the engine)
       │                        │
    ingest/ script/ tts/synth.py tts/voices.py      engine/drivers.py engine/hardware.py
    audio/assemble.py           │                        │
       │                   tts/drama3.py (HTTP)     engine/runner.py (processes)
    audio/ffmpeg.py audio/wavio.py (files, subprocess)
"""

__version__ = "0.3.0"
