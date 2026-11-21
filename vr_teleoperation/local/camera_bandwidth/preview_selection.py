"""Select a documented post-warmup source frame, never the brightest frame."""


def select_preview_row(rows, warmup_seconds=2.0):
    if not rows:
        raise ValueError("No source frames for preview")
    if warmup_seconds < 0:
        raise ValueError("Preview warmup must be non-negative")
    first = rows[0]["timestamp_ns"]
    threshold = first + int(warmup_seconds * 1e9)
    for row in rows:
        if row["timestamp_ns"] >= threshold:
            return row
    raise ValueError(f"No frame after {warmup_seconds:g}s warmup; do not label startup as warmed")
