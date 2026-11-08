"""Device/database and HTTP regressions using synthetic cards, never live mounts."""

import base64
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_web.server import Application
from save_web.service import Service
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


class WebTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'pc'
        self.destination = self.root / 'ps2'
        self.source.mkdir()
        self.destination.mkdir()
        self.service = Service(self.root / 'data')
        self.addCleanup(self.service.close)
        self.app = Application(self.service)
        self.pc = self.service.create_device({'name': 'This machine', 'kind': 'This machine'})
        self.ps2 = self.service.create_device({'name': 'PS2', 'kind': 'PS2', 'poll_seconds': 300})
        self.pc_location = self.service.save_location(self.pc['id'], {'name': 'Emulator', 'path': str(self.source)})
        self.ps2_location = self.service.save_location(self.ps2['id'], {'name': 'VMC', 'path': str(self.destination), 'suffix': '.bin', 'writable': True})

    def request(self, path, body=None, method='GET', headers=None):
        encoded = json.dumps(body).encode() if body is not None else b''
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'HTTP_HOST': 'localhost:8080',
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(encoded)),
                   'wsgi.input': io.BytesIO(encoded), 'HTTP_X_SAVE_MANAGER': '1'}
        environ.update(headers or {})
        response = []
        payload = b''.join(self.app(environ, lambda status, values: response.extend([status, dict(values)])))
        result = json.loads(payload) if 'application/json' in response[1]['Content-Type'] else payload
        return int(response[0][:3]), result, response[1]

    def scan(self, device=None):
        device = device or self.pc
        job = self.service.queue_scan(device['id'])
        self.service.run_scan(job['id'])
        return self.service.db.one('SELECT * FROM jobs WHERE id=?', (job['id'],))

    def cards(self):
        card_file(self.source / 'source.ps2', [generic_save('MY-GAME', b'progress one')], True)
        card_file(self.destination / 'target.bin', [generic_save('OTHER-GAME', b'preserve')], False)
        self.assertEqual(self.scan()['status'], 'done')
        self.assertEqual(self.scan(self.ps2)['status'], 'done')
        inventory = self.service.inventory()
        source_save = next(s for s in inventory['saves'] if s['directory'] == 'MY-GAME')
        target = next(c for c in inventory['cards'] if c['filename'] == 'target.bin')
        return source_save, target

    def test_database_survives_restart_with_cards_and_versions(self):
        self.cards()
        original = self.service.inventory()
        reopened = Service(self.root / 'data')
        self.addCleanup(reopened.close)
        for collection in ['devices', 'locations', 'cards', 'saves']:
            self.assertEqual(original[collection], reopened.inventory()[collection])
        self.assertEqual(original['saves'][0]['version_count'], 1)

    def test_unavailable_location_preserves_last_known_inventory(self):
        self.cards()
        original = self.service.inventory()
        self.source.rename(self.root / 'unplugged')
        self.assertEqual(self.scan()['status'], 'failed')
        inventory = self.service.inventory()
        self.assertEqual(inventory['cards'], original['cards'])
        self.assertEqual(inventory['saves'], original['saves'])
        device = next(d for d in inventory['devices'] if d['id'] == self.pc['id'])
        self.assertEqual(device['status'], 'offline')
        self.assertEqual(device['last_seen'], original['devices'][0]['last_seen'])

    def test_corrupt_card_does_not_replace_snapshot(self):
        self.cards()
        original = self.service.inventory()
        (self.source / 'source.ps2').write_bytes(b'corrupt')
        self.assertEqual(self.scan()['status'], 'failed')
        self.assertEqual(self.service.inventory()['saves'], original['saves'])
        self.assertEqual(self.service.inventory()['devices'][0]['status'], 'error')

    def test_empty_location_and_removed_card_keep_history(self):
        self.cards()
        (self.source / 'source.ps2').unlink()
        self.assertEqual(self.scan()['status'], 'done')
        inventory = self.service.inventory()
        self.assertEqual(inventory['devices'][0]['status'], 'online')
        self.assertFalse(next(c for c in inventory['cards'] if c['filename'] == 'source.ps2')['present'])
        self.assertFalse(next(s for s in inventory['saves'] if s['directory'] == 'MY-GAME')['present'])
        self.assertEqual(len(self.service.db.rows('SELECT * FROM save_versions')), 2)

    def test_marker_detects_empty_unmounted_folder(self):
        self.cards()
        self.service.save_location(self.pc['id'], {'marker': '.online'}, self.pc_location['id'])
        self.assertEqual(self.scan()['status'], 'failed')
        self.assertEqual(self.service.inventory()['devices'][0]['status'], 'offline')
        (self.source / '.online').touch()
        self.assertEqual(self.scan()['status'], 'done')

    def test_multiple_locations_partial_scan_retains_unavailable_location(self):
        self.cards()
        second = self.root / 'second'
        second.mkdir()
        self.service.save_location(self.pc['id'], {'name': 'Second', 'path': str(second)})
        self.source.rename(self.root / 'offline')
        self.assertEqual(self.scan()['status'], 'failed')
        self.assertEqual(self.service.inventory()['devices'][0]['status'], 'partial')
        self.assertTrue(next(s for s in self.service.inventory()['saves'] if s['directory'] == 'MY-GAME')['present'])

    def test_content_versions_do_not_depend_on_card_layout_or_dates(self):
        self.cards()
        self.scan()
        self.assertEqual(next(s for s in self.service.inventory()['saves'] if s['directory'] == 'MY-GAME')['version_count'], 1)
        card_file(self.destination / 'target.bin', [generic_save('MY-GAME', b'progress one')], False)
        self.scan(self.ps2)
        copies = [s for s in self.service.inventory()['saves'] if s['directory'] == 'MY-GAME']
        self.assertEqual(copies[0]['sha256'], copies[1]['sha256'])
        card_file(self.source / 'source.ps2', [generic_save('MY-GAME', b'progress two')], True)
        self.scan()
        updated = next(s for s in self.service.inventory()['saves'] if s['id'] == copies[0]['id'])
        self.assertEqual(updated['version_count'], 2)
        self.assertNotEqual(updated['sha256'], copies[1]['sha256'])

    def test_scheduler_only_queues_due_devices_and_deduplicates(self):
        with patch('save_web.service.apply_copy') as write:
            self.service.enqueue_due(at=1)
            self.service.enqueue_due(at=1)
            jobs = self.service.inventory()['jobs']
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['device_id'], self.ps2['id'])
            self.assertEqual(jobs[0]['reason'], 'poll')
            self.assertEqual(self.service.queue_scan(self.ps2['id'])['id'], jobs[0]['id'])
            self.service.run_scan(jobs[0]['id'])
            self.service.enqueue_due(at=1)
            self.assertEqual(len(self.service.inventory()['jobs']), 1)
            write.assert_not_called()
        self.service.update_device(self.ps2['id'], {'poll_seconds': 0})
        self.service.enqueue_due(at=10**12)
        self.assertEqual(len(self.service.inventory()['jobs']), 1)

    def test_reviewed_http_transfer_backup_and_destination_rescan(self):
        save, target = self.cards()
        before = (self.destination / 'target.bin').read_bytes()
        status, preview, _ = self.request('/api/transfers/preview', {'save_id': save['id'], 'destination_card_id': target['id']}, 'POST')
        self.assertEqual(status, 201, preview)
        self.assertEqual((self.destination / 'target.bin').read_bytes(), before)
        status, error, _ = self.request(f"/api/transfers/{preview['id']}/apply", {}, 'POST')
        self.assertEqual(status, 400, error)
        status, result, _ = self.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle': True}, 'POST')
        self.assertEqual(status, 200, result)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        tree = snapshot(self.destination / 'target.bin')
        self.assertEqual(tree['/MY-GAME/data.bin'][1], b'progress one')
        self.assertEqual(tree['/OTHER-GAME/data.bin'][1], b'preserve')
        self.assertEqual(self.service.inventory()['jobs'][0]['reason'], 'transfer')
        self.service.run_scan(self.service.inventory()['jobs'][0]['id'])
        self.assertEqual(len([s for s in self.service.inventory()['saves'] if s['directory'] == 'MY-GAME']), 2)
        status, _, _ = self.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle': True}, 'POST')
        self.assertEqual(status, 400)

    def test_stale_source_and_readonly_destination_refuse_preview(self):
        save, target = self.cards()
        self.service.save_location(self.ps2['id'], {'writable': False}, self.ps2_location['id'])
        with self.assertRaisesRegex(ValueError, 'read-only'):
            self.service.preview({'save_id':save['id'], 'destination_card_id':target['id']})
        self.service.save_location(self.ps2['id'], {'writable': True}, self.ps2_location['id'])
        with (self.source / 'source.ps2').open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.service.preview({'save_id':save['id'], 'destination_card_id':target['id']})

    def test_destination_change_or_disabled_writes_invalidates_apply(self):
        save, target = self.cards()
        for mode in ['changed', 'readonly']:
            with self.subTest(mode=mode):
                self.scan(self.ps2)
                preview = self.service.preview({'save_id':save['id'], 'destination_card_id':target['id']})
                original = (self.destination / 'target.bin').read_bytes()
                if mode == 'changed':
                    (self.destination / 'target.bin').write_bytes(original + b'changed')
                else:
                    self.service.save_location(self.ps2['id'], {'writable': False}, self.ps2_location['id'])
                with self.assertRaises(ValueError):
                    self.service.apply(preview['id'], {'cards_idle':True})
                self.assertEqual(self.service.db.one('SELECT status FROM transfers WHERE id=?', (preview['id'],))['status'], 'failed')
                self.assertFalse((self.root / 'data' / 'backups').exists())
                (self.destination / 'target.bin').write_bytes(original)

    def test_preview_expiry_cancel_and_restart(self):
        save, target = self.cards()
        body = {'save_id':save['id'], 'destination_card_id':target['id']}
        preview = self.service.preview(body)
        self.service.cancel_preview(preview['id'])
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.service.apply(preview['id'], {'cards_idle':True})
        preview = self.service.preview(body)
        with patch('save_web.service.time.monotonic', return_value=10**20):
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.service.apply(preview['id'], {'cards_idle':True})
        preview = self.service.preview(body)
        job = self.service.queue_scan(self.pc['id'])
        reopened = Service(self.root / 'data')
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.db.one('SELECT status FROM transfers WHERE id=?', (preview['id'],))['status'], 'expired')
        self.assertEqual(reopened.db.one('SELECT status FROM jobs WHERE id=?', (job['id'],))['status'], 'interrupted')

    def test_conflict_requires_explicit_replacement(self):
        save, target = self.cards()
        card_file(self.destination / 'target.bin', [generic_save('MY-GAME', b'different')], False)
        self.scan(self.ps2)
        with self.assertRaisesRegex(ValueError, 'already occupies'):
            self.service.preview({'save_id':save['id'], 'destination_card_id':target['id']})
        preview = self.service.preview({'save_id':save['id'], 'destination_card_id':target['id'], 'conflict':'replace'})
        self.assertEqual(preview['action'], 'Replace existing save')

    def test_web_auth_csrf_and_static_assets(self):
        self.app = Application(self.service, token='secret')
        self.assertEqual(self.request('/api/inventory')[0], 401)
        self.assertEqual(self.request('/healthz')[0], 200)
        credentials = base64.b64encode(b'user:secret').decode()
        headers = {'HTTP_AUTHORIZATION': 'Basic ' + credentials}
        self.assertEqual(self.request('/api/inventory', headers=headers)[0], 200)
        for path in ['/', '/app.js', '/style.css']:
            status, content, response_headers = self.request(path, headers=headers)
            self.assertEqual(status, 200)
            self.assertGreater(len(content), 100)
            self.assertIn("frame-ancestors 'none'", response_headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/devices', {'name':'attacker'}, 'POST', {**headers, 'HTTP_X_SAVE_MANAGER':''})[0], 403)
        self.assertEqual(self.request('/api/devices', {'name':'attacker'}, 'POST', {**headers, 'HTTP_ORIGIN':'http://evil.example'})[0], 403)
        self.assertEqual(self.request('/../README.md', headers=headers)[0], 404)

    def test_validation_and_location_boundaries(self):
        for body in [{'name':''},{'name':'test','poll_seconds':1},{'name':'test','poll_seconds':True}]:
            self.assertEqual(self.request('/api/devices',body,'POST')[0],400)
        for body in [
            {'name':'x','path':'relative'},
            {'name':'x','path':str(self.root / 'data')},
            {'name':'x','path':str(self.source / 'nested')},
            {'name':'x','path':str(self.root / 'other'),'marker':'../escape'},
            {'name':'x','path':str(self.root / 'other'),'suffix':'.mcr'},
        ]:
            with self.assertRaises(ValueError):
                self.service.save_location(self.ps2['id'],body)
        self.assertEqual(self.request('/api/devices/999/scan',{},'POST')[0],404)

    def test_symlink_cards_are_rejected_without_reading_outside_location(self):
        self.cards()
        outside = self.root / 'outside.ps2'
        (self.source / 'source.ps2').rename(outside)
        (self.source / 'source.ps2').symlink_to(outside)
        self.assertEqual(self.scan()['status'], 'failed')
        self.assertIn('regular file', self.service.inventory()['jobs'][0]['error'])

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is installed with the web extra')
    def test_icon_endpoint_is_authenticated_and_remains_available_offline(self):
        from save_web.server import Application
        from PIL import Image
        from test_web_icons import animated_model
        from save_core.save_merge import digest
        model = animated_model()
        key = digest(model)
        with self.service.db.connect() as db:
            db.execute('INSERT INTO icons(sha256,model) VALUES (?,?)', (key,model))
        status, data, headers = self.request(f'/api/icons/{key}.gif')
        self.assertEqual(status, 200)
        self.assertTrue(Image.open(io.BytesIO(data)).is_animated)
        self.assertEqual(headers['Cache-Control'], 'private, max-age=3600')
        status, data, _ = self.request(f'/api/icons/{key}.gif', headers={'QUERY_STRING':'static=1'})
        self.assertEqual(status, 200)
        self.assertEqual(Image.open(io.BytesIO(data)).n_frames, 1)
        self.assertEqual(self.request('/api/icons/unknown.gif')[0], 404)
        self.app = Application(self.service, token='secret')
        self.assertEqual(self.request(f'/api/icons/{key}.gif')[0], 401)

    def test_scan_caches_icon_model_for_offline_rendering(self):
        from test_ffx_slots import source_save
        from test_web_icons import animated_model
        from save_core.save_merge import digest
        model = animated_model()
        save = source_save()
        entry, _ = save.get_file(2)
        entry[2] = len(model)
        save.set_file(2, entry, model)
        card_file(self.source / 'source.ps2', [save], True)
        self.assertEqual(self.scan()['status'], 'done')
        record = self.service.inventory()['saves'][0]
        self.assertEqual(record['icon_hash'], digest(model))
        self.assertEqual(self.service.db.one('SELECT model FROM icons WHERE sha256=?', (record['icon_hash'],))['model'], model)
        self.source.rename(self.root / 'unplugged')
        self.scan()
        self.assertEqual(self.service.inventory()['saves'][0]['icon_hash'], record['icon_hash'])

    def ar_cards(self):
        from test_ar_tonelico import fixture, DIRECTORY
        from test_ar_tonelico_slots import game_save
        card_file(self.source / 'source.ps2', [game_save(fixture(), b'source configuration')], True)
        card_file(self.destination / 'target.bin', [game_save(fixture(), b'target configuration')], False)
        self.scan()
        self.scan(self.ps2)
        inventory = self.service.inventory()
        save = next(s for s in inventory['saves'] if s['card_id'] == next(c['id'] for c in inventory['cards'] if c['filename']=='source.ps2'))
        target = next(c for c in inventory['cards'] if c['filename']=='target.bin')
        return save, target

    def test_slot_http_transfer_inventory_audit_and_offline_retention(self):
        from test_ar_tonelico import fixture, DIRECTORY
        from game_plugins.ar_tonelico import SLOT_SIZE
        save, target = self.ar_cards()
        self.assertEqual(len(self.service.inventory()['slots']), 24)
        before = (self.destination / 'target.bin').read_bytes()
        status, preview, _ = self.request('/api/transfers/preview', {
            'save_id':save['id'], 'destination_card_id':target['id'], 'source_slot':1, 'destination_slot':12}, 'POST')
        self.assertEqual(status,201,preview)
        self.assertEqual((preview['source_slot'],preview['destination_slot']),(1,12))
        status, result, _ = self.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle':True}, 'POST')
        self.assertEqual(status,200,result)
        self.assertEqual(Path(result['backup']).read_bytes(),before)
        self.assertEqual((result['source_slot'],result['destination_slot']),(1,12))
        tree = snapshot(self.destination / 'target.bin')
        self.assertEqual(tree['/'+DIRECTORY+'/GAMEFILE'][1][:-SLOT_SIZE],fixture()[:-SLOT_SIZE])
        self.assertEqual(tree['/'+DIRECTORY+'/'+DIRECTORY][1],b'target configuration')
        self.service.run_scan(self.service.inventory()['jobs'][0]['id'])
        inventory = self.service.inventory()
        target_save = next(s for s in inventory['saves'] if s['card_id']==target['id'])
        copied = next(slot for slot in inventory['slots'] if slot['save_id']==target_save['id'] and slot['number']==12)
        original = next(slot for slot in inventory['slots'] if slot['save_id']==save['id'] and slot['number']==1)
        self.assertEqual(copied['sha256'],original['sha256'])
        self.source.rename(self.root / 'offline')
        self.scan()
        self.assertEqual(inventory['slots'],self.service.inventory()['slots'])

    def test_same_card_slot_copy_requires_writes_and_updates_cached_slots(self):
        save, _ = self.ar_cards()
        body = {'save_id':save['id'], 'destination_card_id':save['card_id'], 'source_slot':1, 'destination_slot':2}
        status, error, _ = self.request('/api/transfers/preview',body,'POST')
        self.assertEqual(status,400,error)
        self.assertIn('read-only',error['error'])
        self.service.save_location(self.pc['id'],{'writable':True},self.pc_location['id'])
        status, preview, _ = self.request('/api/transfers/preview',body,'POST')
        self.assertEqual(status,201,preview)
        self.service.apply(preview['id'],{'cards_idle':True})
        self.service.run_scan(self.service.inventory()['jobs'][0]['id'])
        slots = [s for s in self.service.inventory()['slots'] if s['save_id']==save['id']]
        self.assertEqual([s['number'] for s in slots if s['state']=='occupied'],[1,2,5])

    def test_partial_or_invalid_slot_request_never_falls_back_to_whole_save(self):
        save,target = self.ar_cards()
        original = (self.destination / 'target.bin').read_bytes()
        for fields in [{'source_slot':1}, {'destination_slot':2}, {'source_slot':True,'destination_slot':2},
                       {'source_slot':1,'destination_slot':13}, {'source_slot':1,'destination_slot':2,'conflict':'keep_both'}]:
            status,error,_ = self.request('/api/transfers/preview',{'save_id':save['id'],'destination_card_id':target['id'],**fields},'POST')
            self.assertEqual(status,400,error)
            self.assertEqual((self.destination / 'target.bin').read_bytes(),original)
        self.assertEqual(self.service.inventory()['transfers'],[])


if __name__ == '__main__':
    unittest.main()
