"""Destructive cleanup tests: exclusively temporary fake Codex homes."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'codex/skills/delete-session/scripts/delete_sessions.py'
spec = importlib.util.spec_from_file_location('delete_sessions', SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
A = '01a0e190-3c65-72d2-b462-a6c7e934e1cc'
B = '01a0e190-3c65-72d2-b462-a6c7e934e1cd'
C = '01a0e190-3c65-72d2-b462-a6c7e934e1ce'


@contextmanager
def connection(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        (self.home / 'sqlite').mkdir()
        self.desktop = self.home / 'sqlite/codex-dev.db'
        self.state = self.home / 'state_5.sqlite'
        self.history = self.home / 'thread_history_1.sqlite'
        self.sql(self.desktop, '''
            CREATE TABLE local_thread_catalog(thread_id TEXT, host_id TEXT,
                display_title TEXT, cwd TEXT, project_id TEXT);
            CREATE TABLE local_thread_catalog_scan_entries(thread_id TEXT, host_id TEXT);
            CREATE TABLE thread_timeline_ledger(thread_id TEXT, host_id TEXT);
            CREATE TABLE local_thread_catalog_metadata(id INTEGER, catalog_revision INTEGER);
            INSERT INTO local_thread_catalog_metadata VALUES(1, 10);
            CREATE TABLE local_thread_catalog_sync_state(host_id TEXT,
                watermark_updated_at REAL, initial_build_complete INTEGER,
                observation_sequence INTEGER, last_full_reconciled_at INTEGER);
            INSERT INTO local_thread_catalog_sync_state VALUES('local',1,1,10,1);
            INSERT INTO local_thread_catalog_sync_state VALUES('ssh:remote',1,1,10,1);
            CREATE TABLE local_thread_catalog_scan_checkpoints(host_id TEXT,checkpoint TEXT);
            INSERT INTO local_thread_catalog_scan_checkpoints VALUES('local','checkpoint');
        ''')
        self.sql(self.state, '''
            CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE project_roots(project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, path TEXT);
            CREATE TABLE project_idempotency_keys(key TEXT, project_id TEXT);
            CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT,
                title TEXT, project_id TEXT REFERENCES projects(id) ON DELETE SET NULL);
            CREATE TABLE thread_dynamic_tools(thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE);
            CREATE TABLE thread_attachments(id TEXT, thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE);
            CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT);
            INSERT INTO projects VALUES('p1','WagWag'),('p2','Other'),('p3','Empty');
            INSERT INTO project_roots VALUES('p1','/repo/a'),('p2','/repo/b'),('p3','/repo/empty');
            INSERT INTO project_idempotency_keys VALUES('k1','p1');
        ''')
        self.sql(self.history, 'CREATE TABLE thread_items(thread_id TEXT);')
        self.global_path = self.home / '.codex-global-state.json'
        self.global_path.write_text(json.dumps({
            'local-projects': {'old1': {'id': 'old1', 'name': 'WagWag', 'rootPaths': ['/repo/a']}},
            'app-server-project-id-by-legacy-project-id-by-host': {'local:' + str(self.home): {'old1': 'p1'}},
            'project-order': ['old1'], 'project-appearances': {'old1': {'color': 'red'}},
            'selected-project': {'type': 'local', 'projectId': 'old1'},
            'sidebar-project-thread-orders': {'old1': [A, B]},
            'thread-project-assignments': {A: {'projectKind': 'local', 'projectId': 'old1'}},
            'unrelated-setting': {'keep': True},
        }))
        for name in ('auth.json', 'config.toml', 'AGENTS.md', 'memories_1.sqlite',
                     'plugins/data', 'skills/data', 'memories/data'):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('protected')
        self.add_thread(A, 'First', 'p1', '/repo/a')
        self.add_thread(B, 'Second', 'p1', '/repo/a')
        self.add_thread(C, 'Other', 'p2', '/repo/b')

    def sql(self, path, script):
        with connection(path) as db:
            db.executescript(script)

    def query(self, path, sql):
        with connection(path) as db:
            return db.execute(sql).fetchall()

    def add_thread(self, tid, title, project, cwd):
        path = self.home / 'sessions/2026/09/27' / ('rollout-' + tid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': tid, 'cwd': cwd}}) + '\n')
        with connection(self.desktop) as db:
            db.execute('INSERT INTO local_thread_catalog VALUES(?,?,?,?,?)', (tid, 'local', title, cwd, project))
            for table in ('local_thread_catalog_scan_entries', 'thread_timeline_ledger'):
                db.execute('INSERT INTO ' + table + ' VALUES(?,?)', (tid, 'local'))
        with connection(self.state) as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?)', (tid, str(path), cwd, title, project))
            db.execute('INSERT INTO thread_dynamic_tools VALUES(?)', (tid,))
            db.execute('INSERT INTO thread_attachments VALUES(?,?)', ('attachment-not-a-thread', tid))
        with connection(self.history) as db:
            db.execute('INSERT INTO thread_items VALUES(?)', (tid,))
        return path

    def run_cli(self, *args):
        return cleanup.run([*args, '--codex-home', str(self.home)])

    def snapshot(self):
        return {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}

    def remaining(self):
        return self.query(self.state, 'SELECT id FROM threads ORDER BY id')

    def test_thread_targets(self):
        for target in (A, 'codex://threads/' + A, 'First',
                       'sessions/2026/09/27/rollout-' + A + '.jsonl'):
            with self.subTest(target=target):
                before = self.snapshot()
                code, out = self.run_cli('thread', target, '--dry-run')
                self.assertEqual(code, 0, out)
                self.assertEqual(out['thread_ids'], [A])
                self.assertEqual(self.snapshot(), before)
        code, out = self.run_cli('thread', 'codex://threads/' + A)
        self.assertEqual(code, 0, out)
        self.assertEqual(out['threads_deleted'], 1)
        self.assertEqual(out['rollouts_deleted'], 1)
        self.assertEqual(self.remaining(), [(B,), (C,)])
        self.assertEqual(self.query(self.history, 'SELECT thread_id FROM thread_items'), [(B,), (C,)])
        self.assertEqual(self.query(self.desktop, 'SELECT catalog_revision FROM local_thread_catalog_metadata'), [(11,)])
        self.assertEqual(self.query(self.desktop, 'SELECT initial_build_complete FROM local_thread_catalog_sync_state'), [(1,), (1,)])
        before = self.snapshot()
        self.assertEqual(self.run_cli('thread', A)[0], 1)
        self.assertEqual(self.snapshot(), before)

    def test_exact_title_deletion(self):
        self.assertEqual(self.run_cli('thread', 'First')[0], 0)
        self.assertEqual(self.remaining(), [(B,), (C,)])

    def test_uuid_deletion(self):
        self.assertEqual(self.run_cli('thread', A)[0], 0)
        self.assertEqual(self.remaining(), [(B,), (C,)])

    def test_ambiguous_title(self):
        self.sql(self.desktop, "UPDATE local_thread_catalog SET display_title='Same'")
        before = self.snapshot()
        code, out = self.run_cli('thread', 'Same')
        self.assertEqual(code, 2, out)
        self.assertEqual(len(out['matches']), 3)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.run_cli('thread', 'Sam')[0], 1)

    def test_ghost(self):
        for p in self.home.rglob('*' + A + '.jsonl'):
            p.unlink()
        code, out = self.run_cli('thread', A)
        self.assertEqual(code, 0, out)
        self.assertEqual(out['rollout'], 'already_missing')

    def test_rollout_without_databases(self):
        for p in (self.desktop, self.state, self.history):
            p.unlink()
        code, out = self.run_cli('thread', A)
        self.assertEqual(code, 0, out)
        self.assertEqual(out['rollouts_deleted'], 1)

    def test_project_name_and_path(self):
        for target in ('WagWag', '/repo/a', '/repo/a/../a/'):
            code, out = self.run_cli('project', target, '--dry-run')
            self.assertEqual(code, 0, out)
            self.assertEqual(out['thread_ids'], [A, B])
        before = self.query(self.state, 'SELECT * FROM projects')
        code, out = self.run_cli('project', 'WagWag')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remaining(), [(C,)])
        self.assertEqual(self.query(self.state, 'SELECT * FROM projects'), before)
        self.assertIn('old1', json.loads(self.global_path.read_text())['local-projects'])

    def test_duplicate_project(self):
        self.sql(self.state, "UPDATE projects SET name='WagWag' WHERE id='p2'")
        before = self.snapshot()
        self.assertEqual(self.run_cli('project', 'WagWag')[0], 2)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_project_deletion(self):
        code, out = self.run_cli('project', 'WagWag', '--delete-projects')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['projects_deleted'], 1)
        self.assertEqual(self.query(self.state, 'SELECT id FROM projects'), [('p2',), ('p3',)])
        state = json.loads(self.global_path.read_text())
        self.assertEqual(state['local-projects'], {})
        self.assertEqual(state['project-order'], [])
        self.assertNotIn('selected-project', state)
        self.assertEqual(self.query(self.state, 'SELECT * FROM project_idempotency_keys'), [])

    def test_empty_project(self):
        code, out = self.run_cli('project', 'Empty', '--delete-projects')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['threads_deleted'], 0)
        self.assertEqual(out['projects_deleted'], 1)

    def test_global_preserves_projects_and_remote(self):
        self.sql(self.desktop, "INSERT INTO local_thread_catalog VALUES('" + A + "','ssh:remote','Remote','/repo/a','p1')")
        projects = self.query(self.state, 'SELECT * FROM projects')
        code, out = self.run_cli('all')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['threads_deleted'], 3)
        self.assertEqual(self.remaining(), [])
        self.assertEqual(self.query(self.state, 'SELECT * FROM projects'), projects)
        self.assertEqual(self.query(self.desktop, 'SELECT host_id FROM local_thread_catalog'), [('ssh:remote',)])
        self.assertEqual(self.query(self.desktop, 'SELECT initial_build_complete FROM local_thread_catalog_sync_state'), [(0,), (1,)])
        self.assertEqual(self.query(self.desktop, 'SELECT * FROM local_thread_catalog_scan_checkpoints'), [])
        for name in ('auth.json', 'config.toml', 'AGENTS.md', 'memories_1.sqlite', 'plugins/data', 'skills/data', 'memories/data'):
            self.assertEqual((self.home / name).read_text(), 'protected')

    def test_global_delete_projects(self):
        code, out = self.run_cli('all', '--delete-projects')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['projects_deleted'], 3)
        self.assertEqual(self.query(self.state, 'SELECT * FROM projects'), [])
        self.assertEqual(json.loads(self.global_path.read_text())['local-projects'], {})

    def test_dry_runs(self):
        for args in [('thread', A), ('project', 'WagWag'), ('all',),
                     ('project', 'WagWag', '--delete-projects'), ('all', '--delete-projects')]:
            before = self.snapshot()
            code, out = self.run_cli(*args, '--dry-run')
            self.assertEqual(code, 0, out)
            self.assertEqual(self.snapshot(), before)

    def test_unknown_schema(self):
        self.sql(self.desktop, 'ALTER TABLE local_thread_catalog RENAME COLUMN thread_id TO unknown_id')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all')[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_thread_table(self):
        self.sql(self.state, 'CREATE TABLE future_thread_data(thread_id TEXT)')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all')[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_unsupported_project_deletion(self):
        self.global_path.unlink()
        self.sql(self.state, 'DROP TABLE projects; DROP TABLE project_roots; DROP TABLE project_idempotency_keys;')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all', '--delete-projects')[0], 7)
        self.assertEqual(self.snapshot(), before)

    def test_partial_failure(self):
        original = Path.unlink
        def fail_rollout(path, *args, **kwargs):
            if path.suffix == '.jsonl':
                raise PermissionError('injected rollout failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_rollout):
            code, out = self.run_cli('all', '--delete-projects')
        self.assertEqual(code, 8, out)
        self.assertEqual(out['status'], 'partial_failure')
        self.assertEqual(len(self.query(self.state, 'SELECT * FROM projects')), 3)
        self.assertEqual(self.run_cli('all')[0], 0)

    def test_invalid_input_json(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'thread', A, '--delete-projects'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)['status'], 'invalid_input')

    def test_database_and_filesystem_errors(self):
        with patch.object(cleanup.sqlite3, 'connect', side_effect=sqlite3.OperationalError('injected')):
            self.assertEqual(self.run_cli('all')[0], 4)
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('injected')):
            self.assertEqual(self.run_cli('all')[0], 5)

    def test_outside_rollout_fails_closed(self):
        self.sql(self.state, "UPDATE threads SET rollout_path='/tmp/unrelated.jsonl'")
        before = self.snapshot()
        self.assertEqual(self.run_cli('all')[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_project_relationship(self):
        self.sql(self.state, 'CREATE TABLE future_project_data(owner TEXT REFERENCES projects(id))')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all', '--delete-projects')[0], 7)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.run_cli('all')[0], 0)

    def test_unknown_thread_relationship(self):
        self.sql(self.state, 'CREATE TABLE future_data(owner TEXT REFERENCES threads(id) ON DELETE CASCADE)')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all')[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_archived_rollout(self):
        path = next(self.home.rglob('*' + A + '.jsonl'))
        archive = self.home / 'archived_sessions' / path.name
        archive.parent.mkdir()
        path.rename(archive)
        code, out = self.run_cli('thread', str(archive))
        self.assertEqual(code, 0, out)
        self.assertFalse(archive.exists())

    def test_assignment_overrides_cwd_and_stale_legacy(self):
        self.sql(self.state, "UPDATE threads SET project_id='p2' WHERE id='" + A + "'")
        code, out = self.run_cli('project', 'WagWag', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['thread_ids'], [B])

    def test_concurrent_global_state_change(self):
        original = cleanup.Store.delete_threads
        def concurrent(store, *args):
            result = original(store, *args)
            data = json.loads(store.global_path.read_text())
            data['external-change'] = True
            store.global_path.write_text(json.dumps(data))
            return result
        with patch.object(cleanup.Store, 'delete_threads', concurrent):
            code, out = self.run_cli('thread', A)
        self.assertEqual(code, 8, out)
        self.assertTrue(json.loads(self.global_path.read_text())['external-change'])

    def test_database_rollback_before_first_commit(self):
        self.sql(self.desktop, "CREATE UNIQUE INDEX one_id ON local_thread_catalog(thread_id)")
        # A failure before the first commit must roll back earlier row deletes.
        before = self.snapshot()
        original = cleanup.Store.predicate
        def invalid(store, kind, table, tid):
            if table == 'thread_timeline_ledger':
                return 'missing_column=?', [tid]
            return original(store, kind, table, tid)
        with patch.object(cleanup.Store, 'predicate', invalid):
            code, out = self.run_cli('thread', A)
        self.assertEqual(code, 4, out)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_rollout_fails_closed(self):
        next(self.home.rglob('*' + A + '.jsonl')).write_text('[]')
        before = self.snapshot()
        self.assertEqual(self.run_cli('all')[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_no_catalog_project_fallback(self):
        self.state.unlink()
        self.global_path.unlink()
        code, out = self.run_cli('project', '/repo/a', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['thread_ids'], [A, B])

    def test_catalog_rollout_identity_mismatch(self):
        other = next(self.home.rglob('*' + B + '.jsonl'))
        with connection(self.state) as db:
            db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(other), A))
        before = self.snapshot()
        self.assertEqual(self.run_cli('thread', A)[0], 6)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_rollout(self):
        path = next(self.home.rglob('*' + A + '.jsonl'))
        path.unlink()
        path.symlink_to(self.home / 'auth.json')
        before = self.snapshot()
        self.assertEqual(self.run_cli('thread', A)[0], 6)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
