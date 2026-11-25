"""Time-driven braille spinner and a soft highlight sweep for activity labels."""
import math

FRAMES = tuple("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
FRAME_INTERVAL = 0.04


def activity_fragments(label, elapsed, tokens=0):
    elapsed = max(0, elapsed)
    frame = int(elapsed / FRAME_INTERVAL)
    fragments = [("class:accent", FRAMES[frame % len(FRAMES)] + " ")]
    position = (elapsed * 10) % (len(label) + 10)
    base = (154, 165, 176)
    for index, char in enumerate(label):
        intensity = math.exp(-((index - position) / 2.5) ** 2)
        rgb = tuple(round(value + (240 - value) * intensity) for value in base)
        fragments.append(("fg:#{:02x}{:02x}{:02x}".format(*rgb), char))
    suffix = f"  {elapsed:.1f}s"
    if tokens:
        suffix += f" · {tokens:,} tokens"
    fragments.append(("class:muted", suffix))
    return fragments
