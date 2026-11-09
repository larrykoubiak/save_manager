from copy import deepcopy
import unittest

from save_core.save_selection import select_versions
from test_save_copy import generic_save


def item(name, year, side='VMC', file_year=None):
    save = generic_save(name, b'content')
    save.dirent[6] = (0, 0, 12, 1, 1, year)
    entry, data = save.get_file(0)
    entry[6] = (0, 0, 12, 1, 1, file_year or year)
    save.set_file(0, entry, data)
    return {'name': name, 'save': save, 'sources': [{'side': side}]}


class SaveSelectionTest(unittest.TestCase):
    def test_newest_internal_file_date_wins_without_modifying_input(self):
        older = item('UNSUPPORTED-GAME', 2024)
        newest = item('UNSUPPORTED-GAME', 2019, file_year=2026)
        before = deepcopy(list(newest['save']))
        result = select_versions([older, newest])
        self.assertIs(result.kept[0], newest)
        self.assertIs(result.archived[0], older)
        self.assertEqual(list(newest['save']), before)

    def test_explicit_pi_odin_choice_overrides_dates(self):
        pi = item('BASLUS-21577', 2020, 'Pi')
        other = item('BASLUS-21577', 2026)
        result = select_versions([other, pi], {'BASLUS-21577': 'Pi'})
        self.assertEqual(result.kept, (pi,))
        self.assertEqual(result.archived, (other,))
        with self.assertRaisesRegex(ValueError, 'Expected one'):
            select_versions([other], {'BASLUS-21577': 'Pi'})

    def test_metadata_of_duplicate_source_copies_counts_too(self):
        first, second = item('UNSUPPORTED-GAME', 2020), item('UNSUPPORTED-GAME', 2026)
        first['sources'].append({'side': 'PCSX2', 'latest_updated': (0, 0, 12, 1, 1, 2027)})
        self.assertEqual(select_versions([first, second]).kept, (first,))

    def test_plugin_versions_and_separately_named_slots_are_preserved(self):
        ar = [item('BASLUS-21445AT', year) for year in (2015, 2026)]
        ffx = [item('BISLPS-25088FF090600', year) for year in (2013, 2026)]
        slots = [item('BASLUS-20370-' + str(n), 2020 + n) for n in (1, 2)]
        steambot = [item('BASLUS-21344', year) for year in (2013, 2019)]
        result = select_versions(ar + ffx + slots + steambot)
        self.assertEqual(len(result.kept), 8)
        self.assertFalse(result.archived)

    def test_tied_and_missing_dates_are_retained_for_review(self):
        first, second = item('UNSUPPORTED-GAME', 2026), item('UNSUPPORTED-GAME', 2026)
        result = select_versions([first, second])
        self.assertEqual(len(result.kept), 2)
        self.assertTrue(result.notes)
        unknown = item('UNSUPPORTED-GAME', 0)
        result = select_versions([first, unknown])
        self.assertEqual(len(result.kept), 2)
