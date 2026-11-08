from datetime import datetime
import unittest

from save_core.save_games import game_id, group_games


def record(save, card='Mcd001.ps2', title='Game [01] Location'):
    return {'save': save, 'card': card, 'title': title, 'updated': datetime(2026, 1, 1)}


class GameGroupingTest(unittest.TestCase):
    def test_slots_and_duplicate_cards_stay_visible_under_one_game(self):
        left = [record('BISLPS-25088FF090600')]
        right = [record('BISLPS-25088FF090600', 'game.bin'),
                 record('BISLPS-25088FF090601', 'game.bin'),
                 record('BISLPS-25088FF090600', 'generic_0.bin')]
        games = group_games(left, right, {'SLPS-25088': 'Final Fantasy X'})
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]['title'], 'Final Fantasy X')
        self.assertEqual(games[0]['present'], 'Both')
        self.assertEqual(len(games[0]['Emulator']), 1)
        self.assertEqual(len(games[0]['VMC']), 3)

    def test_distinct_regions_and_unknown_ids_are_not_merged_by_title(self):
        records = [record('BASLUS-20370-01'), record('BESLES-50967-01'),
                   record('BEDATA-SYSTEM'), record('OTHER-SAVE')]
        self.assertEqual(len(group_games(records, [])), 4)
        self.assertEqual(game_id('SLUS_203.70_0.bin'), 'SLUS-20370')
        self.assertEqual(game_id('BASLUS-20370-01'), 'SLUS-20370')

    def test_a_missing_icon_does_not_hide_the_game_or_other_copies_icon(self):
        left = record('BASLUS-21577')
        right = dict(record('BASLUS-21577', 'game.bin'), icon=b'preview')
        game = group_games([left], [right])[0]
        self.assertEqual(game['icon'], b'preview')
        self.assertEqual(game['title'], 'Game')
