import unittest
from storyboard import BEATS, build_storyboard

def assets(count):
    return {"assets": [
        {"asset_id": f"a{i}", "path": f"screenshots/{i:02}.png",
         "source_url": "https://example.com/source", "scroll_y": i * 500}
        for i in range(count)
    ]}

class StoryboardTests(unittest.TestCase):
    def test_every_asset_is_used_once_when_assets_exceed_beats(self):
        plan = build_storyboard(assets(30), 57.37585)
        paths = [s["path"] for s in plan["timeline"] if s["kind"] == "image"]
        self.assertEqual(len(paths), 30)
        self.assertEqual(len(set(paths)), 30)
        self.assertEqual(plan["assets_used"], 30)

    def test_fewer_assets_produce_intertitles_for_uncovered_beats(self):
        plan = build_storyboard(assets(4), 57.37585)
        self.assertEqual(plan["assets_used"], 4)
        self.assertEqual(sum(s["kind"] == "intertitle" for s in plan["timeline"]), 9)

    def test_timeline_is_ordered_and_covers_narration(self):
        shots = build_storyboard(assets(17), 57.37585)["timeline"]
        self.assertAlmostEqual(shots[0]["start"], 0.0)
        self.assertAlmostEqual(shots[-1]["end"], 57.37585, places=2)
        for previous, current in zip(shots, shots[1:]):
            self.assertAlmostEqual(previous["end"], current["start"], places=2)
        self.assertTrue(all(s["end"] > s["start"] for s in shots))

    def test_visible_source_text_matches_the_relevant_narration_beat(self):
        source_assets = {
            "assets": [
                {"asset_id": f"beat-{i}", "path": f"source-{i}.png",
                 "visible_text": beat["text"], "source_title": "", "source_url": ""}
                for i, beat in enumerate(BEATS)
            ]
        }
        plan = build_storyboard(source_assets, 57.37585)
        assignments = {
            shot["asset_id"]: shot["beat_index"]
            for shot in plan["timeline"] if shot["kind"] == "image"
        }
        for index in range(len(BEATS)):
            self.assertEqual(assignments[f"beat-{index}"], index)

    def test_empty_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            build_storyboard({"assets": []}, 57.3)

if __name__ == "__main__":
    unittest.main()
