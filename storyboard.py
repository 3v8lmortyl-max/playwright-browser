"""Deterministic documentary storyboard planner. Every captured image is assigned once."""
from __future__ import annotations
from typing import Any
import json
from pathlib import Path
import re

BEATS = [
    {"start": 0.25, "caption_end": 3.40, "text": "Artificial intelligence is changing cybersecurity."},
    {"start": 3.75, "caption_end": 8.68, "text": "But what happens when the same technology is used against the institutions it was meant to protect?"},
    {"start": 8.99, "caption_end": 13.99, "text": "On October twenty-second, a cyber campaign targeting the European Commission was detected."},
    {"start": 14.33, "caption_end": 19.65, "text": "Nearly a month later, officials confirmed something significant: AI tools had been used."},
    {"start": 19.94, "caption_end": 23.78, "text": "Several internal applications were temporarily taken offline as a precaution."},
    {"start": 24.46, "caption_end": 28.82, "text": "Most have since been restored. The investigation, however, is still underway."},
    {"start": 29.36, "caption_end": 31.84, "text": "Authorities have not publicly identified the attackers."},
    {"start": 32.45, "caption_end": 34.72, "text": "Nor have they established the full extent of the incident."},
    {"start": 35.31, "caption_end": 39.63, "text": "The Commission says it has found no evidence of a data breach affecting its staff as a whole."},
    {"start": 40.29, "caption_end": 45.08, "text": "The same technology can help defenders discover weaknesses—or help attackers find them first."},
    {"start": 45.84, "caption_end": 48.92, "text": "The difference is increasingly about who controls the tools."},
    {"start": 49.30, "caption_end": 52.55, "text": "AI is becoming part of the cybersecurity battlefield."},
    {"start": 52.81, "caption_end": 56.65, "text": "And the race to defend our digital infrastructure has already begun."},
]

def _allocate_counts(total: int, weights: list[float]) -> list[int]:
    if total < 0:
        raise ValueError("total cannot be negative")
    if not weights:
        return []
    if total == 0:
        return [0] * len(weights)
    if total < len(weights):
        counts = [0] * len(weights)
        for index in sorted(range(len(weights)), key=lambda i: (-weights[i], i))[:total]:
            counts[index] = 1
        return counts
    counts = [1] * len(weights)
    remaining = total - len(weights)
    exact = [remaining * (w / sum(weights)) for w in weights]
    whole = [int(value) for value in exact]
    counts = [base + extra for base, extra in zip(counts, whole)]
    left = remaining - sum(whole)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - whole[i]), i))
    for index in order[:left]:
        counts[index] += 1
    return counts

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "did", "does", "for", "from", "had", "has", "have", "he", "her", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "more", "most",
    "not", "of", "on", "or", "our", "out", "she", "so", "than", "that",
    "the", "their", "them", "there", "they", "this", "to", "under", "up",
    "us", "was", "were", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your",
}

def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{2,}", text.lower())
        if token not in STOP_WORDS
    }

def build_storyboard(manifest: dict[str, Any], duration: float) -> dict[str, Any]:
    if duration <= 0:
        raise ValueError("Narration duration must be positive")
    assets = manifest.get("assets", [])
    if not assets:
        raise ValueError("Capture manifest contains no assets")

    beat_starts = [0.0] + [float(beat["start"]) for beat in BEATS[1:]]
    boundaries = beat_starts + [duration]
    weights = [max(0.05, boundaries[i + 1] - boundaries[i]) for i in range(len(BEATS))]
    counts = _allocate_counts(len(assets), weights)

    # Match each viewport capture to the narration beat using extracted visible
    # text, page title and URL. A stable capture-order tie-breaker keeps output
    # deterministic when a page contains little readable text.
    remaining = list(range(len(assets)))
    assigned: dict[int, list[tuple[dict[str, Any], float, list[str]]]] = {}
    for beat_index, count in enumerate(counts):
        if count == 0:
            assigned[beat_index] = []
            continue
        beat_terms = _tokens(BEATS[beat_index]["text"])
        candidates = []
        for asset_index in remaining:
            asset = assets[asset_index]
            source_text = " ".join([
                asset.get("visible_text", ""),
                asset.get("source_title", ""),
                asset.get("source_url", ""),
            ])
            matched = sorted(beat_terms & _tokens(source_text))
            score = len(matched) / max(1, len(beat_terms))
            candidates.append((score, asset_index, matched))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected = candidates[:count]
        selected.sort(key=lambda item: item[1])
        assigned[beat_index] = [
            (assets[index], score, matched) for score, index, matched in selected
        ]
        selected_indices = {index for _, index, _ in selected}
        remaining = [index for index in remaining if index not in selected_indices]

    timeline = []
    asset_index = 0
    for beat_index, beat in enumerate(BEATS):
        start, end, count = boundaries[beat_index], boundaries[beat_index + 1], counts[beat_index]
        if count == 0:
            timeline.append({"kind": "intertitle", "start": round(start, 3), "end": round(end, 3),
                             "text": beat["text"], "beat_index": beat_index})
            continue
        slot = (end - start) / count
        for slot_index, (asset, score, matched) in enumerate(assigned[beat_index]):
            asset_index += 1
            timeline.append({
                "kind": "image",
                "start": round(start + slot * slot_index, 3),
                "end": round(end if slot_index == count - 1 else start + slot * (slot_index + 1), 3),
                "path": asset["path"],
                "asset_id": asset.get("asset_id", asset["path"]),
                "source_url": asset.get("source_url", ""),
                "source_title": asset.get("source_title", ""),
                "scroll_y": asset.get("scroll_y", 0),
                "beat_index": beat_index,
                "relevance_score": round(score, 3),
                "matched_terms": matched,
            })
    if asset_index != len(assets) or remaining:
        raise AssertionError(f"Planner used {asset_index} of {len(assets)} assets")

    captions = []
    for index, beat in enumerate(BEATS, start=1):
        start = max(0.0, min(float(beat["start"]), duration))
        end = max(start + 0.05, min(float(beat["caption_end"]), duration))
        if start < duration:
            captions.append({"index": index, "start": round(start, 3),
                             "end": round(end, 3), "text": beat["text"]})
    return {
        "version": 1, "duration": round(duration, 3),
        "canvas": {"width": 1080, "height": 1920, "fps": 24},
        "asset_count": len(assets), "assets_used": asset_index,
        "timeline": timeline, "captions": captions,
    }

def load_capture_manifest(path: str | Path = "capture_manifest.json") -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
