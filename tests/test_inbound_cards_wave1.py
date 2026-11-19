from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

import pandas as pd
from dash import html

import app


def _df(rows):
    return pd.DataFrame(rows)


class ArrivalTransitionTests(unittest.TestCase):
    def setUp(self):
        app._ARRIVAL_FIRED.update({"df_id": None, "due": frozenset()})

    def test_due_set_is_vectorised_and_ignores_missing_times(self):
        now = datetime(2026, 11, 19, 12, 0)
        df = _df([
            dict(awb="A", is_en_route=True, truck_arrival_time=now - timedelta(minutes=1)),
            dict(awb="B", is_en_route=True, truck_arrival_time=now + timedelta(minutes=5)),
            dict(awb="C", is_en_route=False, truck_arrival_time=now - timedelta(hours=1)),
            dict(awb="D", is_en_route=True, truck_arrival_time=None),
        ])
        self.assertEqual(app._due_arrival_awbs(df, now), frozenset({"A"}))
        self.assertEqual(app._due_arrival_awbs(None, now), frozenset())
        self.assertEqual(app._due_arrival_awbs(_df([dict(awb="X")]), now), frozenset())

    def test_transition_fires_once_per_new_due_set(self):
        df = _df([dict(awb="A")])
        self.assertFalse(app._arrival_transition_should_fire(df, frozenset()))
        self.assertTrue(app._arrival_transition_should_fire(df, frozenset({"A"})))
        # Same frozen parse, same due set: later polls must stay quiet.
        self.assertFalse(app._arrival_transition_should_fire(df, frozenset({"A"})))
        self.assertTrue(app._arrival_transition_should_fire(df, frozenset({"A", "B"})))
        self.assertFalse(app._arrival_transition_should_fire(df, frozenset({"B"})))
        # A new source read (new DataFrame) starts a fresh signature.
        other = _df([dict(awb="A")])
        self.assertTrue(app._arrival_transition_should_fire(other, frozenset({"A"})))

    def test_gate_bumps_version_once_and_never_forces_render(self):
        now = datetime.now()
        df = _df([dict(awb="A", is_en_route=True, truck_arrival_time=now - timedelta(minutes=1))])
        with patch.object(app.data_cache, "bump_data_version") as bump:
            results = [app._has_due_arrival_transition(df) for _ in range(5)]
        self.assertEqual(results, [False] * 5)
        self.assertEqual(bump.call_count, 1)


class CardKeyTests(unittest.TestCase):
    def test_cards_get_stable_unique_ids_from_card_key(self):
        cards = [
            html.Div(**{"data-card-key": "111"}),
            html.Div(**{"data-card-key": "outbound:ABC"}),
            html.Div(**{"data-card-key": "111"}),
            html.Div(),
        ]
        app._key_cards(cards)
        self.assertEqual(cards[0].id, {"type": "prio-card", "index": "111"})
        self.assertEqual(cards[1].id, {"type": "prio-card", "index": "outbound:ABC"})
        self.assertEqual(cards[2].id, {"type": "prio-card", "index": "111#1"})
        self.assertIsNone(getattr(cards[3], "id", None))

    def test_grid_ids_follow_awb_not_position(self):
        first = app._cards_grid([html.Div(**{"data-card-key": k}) for k in "ABCD"])
        second = app._cards_grid([html.Div(**{"data-card-key": k}) for k in "ACD"])
        ids = lambda grid: [c.id["index"] for c in grid.children[0].children]
        self.assertEqual(ids(first), ["A", "B", "C", "D"])
        self.assertEqual(ids(second), ["A", "C", "D"])

    def test_note_input_persists_draft_until_note_list_changes(self):
        with patch.object(app.notes_manager, "get_notes", return_value=[]):
            empty = app._make_notes_block("A")
        with patch.object(app.notes_manager, "get_notes", return_value=[{"id": "n1", "text": "x"}]):
            one = app._make_notes_block("A")

        def note_input(block):
            found = []

            def walk(node):
                if getattr(node, "id", None) == {"type": "note-input", "index": "A"}:
                    found.append(node)
                children = getattr(node, "children", None)
                for child in children if isinstance(children, list) else [children]:
                    if child is not None and hasattr(child, "to_plotly_json"):
                        walk(child)
            walk(block)
            return found[0]

        self.assertEqual(note_input(empty).persistence_type, "memory")
        self.assertTrue(note_input(empty).persistence)
        self.assertNotEqual(note_input(empty).persistence, note_input(one).persistence)


if __name__ == "__main__":
    unittest.main()
