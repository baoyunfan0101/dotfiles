#!/usr/bin/env python3
"""Deterministic local Codex cleanup. See --help for the public interface."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
TABLES = {
    'desktop': {
        'local_thread_catalog': ('thread_id', 'host_id', 'display_title', 'cwd'),
        'local_thread_catalog_scan_entries': ('thread_id', 'host_id'),
        'thread_timeline_ledger': ('thread_id', 'host_id'),
    },
    'state': {
        'threads': ('id', 'rollout_path', 'cwd', 'title'),
        'thread_dynamic_tools': ('thread_id',),
        'thread_attachments': ('thread_id',),
        'thread_spawn_edges': ('parent_thread_id', 'child_thread_id'),
    },
    'history': {name: ('thread_id',) for name in (
        'thread_turns', 'thread_items', 'thread_history_projection_state',
        'thread_realtime_items')},
}
DB_PATHS = {'desktop': 'sqlite/codex-dev.db', 'state': 'state_5.sqlite',
            'history': 'thread_history_1.sqlite'}
AUX = {
    'local_thread_catalog_metadata': ('id', 'catalog_revision'),
    'local_thread_catalog_sync_state': ('host_id', 'watermark_updated_at',
        'initial_build_complete', 'observation_sequence'),
    'local_thread_catalog_hosts': ('host_id', 'host_kind'),
    'local_thread_catalog_scan_checkpoints': ('host_id', 'checkpoint'),
    'projects': ('id', 'name'), 'project_roots': ('project_id', 'path'),
    'project_idempotency_keys': ('project_id',),
}


class Failure(Exception):
    def __init__(self, code, status, **details):
        self.code, self.result = code, dict(status=status, **details)


def fail_schema(message):
    raise Failure(6, 'unsupported_schema', detail=message)


def norm(path):
    return str(Path(path).expanduser().resolve())


def unique(matches):
    if not matches:
        raise Failure(1, 'not_found')
    if len(matches) > 1:
        raise Failure(2, 'ambiguous', matches=matches)
    return matches[0]


class Store:
    def __init__(self, home):
        self.home = home.resolve()
        self.dbs, self.schema = {}, {}
        self.rollouts, self.rows = {}, {}
        self.projects = []
        self.global_path = self.home / '.codex-global-state.json'
        self.global_bytes = None
        self.global_state = {}
        self.committed = False
        self.unknown_project_tables = []

    def close(self):
        for db in self.dbs.values():
            db.close()

    def load(self):
        # Do not silently accept a new version alongside an old database.
        for prefix, expected in [('state_', 'state_5.sqlite'),
                                 ('thread_history_', 'thread_history_1.sqlite')]:
            if any(p.name != expected for p in self.home.glob(prefix + '*.sqlite')):
                fail_schema('unknown database version: ' + prefix)
        for kind, relative in DB_PATHS.items():
            path = self.home / relative
            if not path.exists():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.home):
                fail_schema('database outside Codex home')
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            self.dbs[kind] = db
            schema = self.schema[kind] = {}
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                name = row[0]
                # Identifiers come from SQLite metadata, never from CLI targets.
                quoted = name.replace('"', '""')
                schema[name] = {r[1] for r in db.execute(f'PRAGMA table_info("{quoted}")')}
                for fk in db.execute(f'PRAGMA foreign_key_list("{quoted}")'):
                    if fk[2] == 'threads' and name not in TABLES[kind]:
                        fail_schema('unknown thread relationship: ' + name)
                    if fk[2] == 'projects' and name not in (*TABLES[kind], *AUX):
                        self.unknown_project_tables.append(name)
            if not set(TABLES[kind]) & schema.keys():
                fail_schema('no supported tables in ' + relative)
            for name, cols in {**TABLES[kind], **AUX}.items():
                if name in schema and not set(cols) <= schema[name]:
                    fail_schema('incompatible table: ' + name)
            for name, cols in schema.items():
                if kind == 'state' and 'project_id' in cols and name not in (*TABLES[kind], *AUX):
                    self.unknown_project_tables.append(name)
                if name not in TABLES[kind] and name not in AUX and (
                        'thread_id' in cols or 'parent_thread_id' in cols):
                    fail_schema('unknown thread table: ' + name)
            for trigger in db.execute("SELECT tbl_name FROM sqlite_master WHERE type='trigger'"):
                if trigger[0] in (*TABLES[kind], *AUX):
                    fail_schema('unsupported mutation trigger: ' + trigger[0])
            if kind == 'desktop' and 'local_thread_catalog' in schema:
                if 'local_thread_catalog_metadata' not in schema:
                    fail_schema('catalog revision table missing')
                if db.execute('SELECT count(*) FROM local_thread_catalog_metadata WHERE id=1').fetchone()[0] != 1:
                    fail_schema('catalog revision row missing')
        desktop = self.dbs.get('desktop')
        self.local_hosts = {'local', 'local:' + str(self.home)}
        if desktop and 'local_thread_catalog_hosts' in self.schema['desktop']:
            self.local_hosts.update(r[0] for r in desktop.execute(
                "SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind='local'"))
        for kind, tables in TABLES.items():
            if kind not in self.dbs:
                continue
            for table in tables:
                if table not in self.schema[kind]:
                    continue
                columns = list(TABLES[kind][table])
                if 'project_id' in self.schema[kind][table]:
                    columns.append('project_id')
                for row in self.dbs[kind].execute('SELECT DISTINCT ' + ','.join(columns) + ' FROM ' + table):
                    row = dict(row)
                    if kind == 'desktop' and row['host_id'] not in self.local_hosts:
                        continue
                    ids = [row['id'] if table == 'threads' else row.get('thread_id')]
                    if table == 'thread_spawn_edges':
                        ids = [row['parent_thread_id'], row['child_thread_id']]
                    for tid in ids:
                        if not isinstance(tid, str) or not UUID.fullmatch(tid):
                            fail_schema('invalid stored thread id')
                        self.rows.setdefault(tid, []).append(row)
                    if table == 'threads':
                        self.add_rollout(row['id'], Path(row['rollout_path']))
        for root in ('sessions', 'archived_sessions'):
            directory = self.home / root
            if directory.is_symlink():
                fail_schema('symlinked session directory')
            if not directory.exists():
                continue
            for path in directory.rglob('*.jsonl'):
                match = re.search(r'(' + UUID.pattern + r')\.jsonl$', path.name)
                if path.is_symlink() or not path.resolve().is_relative_to(directory):
                    fail_schema('symlinked rollout path')
                with path.open() as stream:
                    try:
                        first = json.loads(stream.readline())
                    except (ValueError, UnicodeError):
                        fail_schema('invalid rollout metadata: ' + str(path))
                if not isinstance(first, dict):
                    fail_schema('invalid rollout record')
                meta = first.get('payload', {}) if first.get('type') == 'session_meta' else {}
                if not isinstance(meta, dict):
                    fail_schema('invalid rollout metadata')
                tid = meta.get('id') or (match[1] if match else None)
                if not isinstance(tid, str) or not UUID.fullmatch(tid):
                    fail_schema('unrecognized rollout: ' + str(path))
                if match and match[1] != tid:
                    fail_schema('rollout identity mismatch')
                self.add_rollout(tid, path)
                self.rows.setdefault(tid, []).append({'cwd': meta.get('cwd')})
        if self.global_path.exists():
            if self.global_path.is_symlink():
                fail_schema('symlinked global state')
            self.global_bytes = self.global_path.read_bytes()
            try:
                self.global_state = json.loads(self.global_bytes)
            except ValueError:
                fail_schema('invalid global state')
            if not isinstance(self.global_state, dict):
                fail_schema('invalid global state')
        owners = {}
        for tid, paths in self.rollouts.items():
            for path in paths:
                if path in owners and owners[path] != tid:
                    fail_schema('rollout claimed by multiple threads')
                owners[path] = tid
        self.load_projects()

    def add_rollout(self, tid, path):
        if not path.is_absolute():
            path = self.home / path
        resolved = path.resolve()
        match = re.search(r'(' + UUID.pattern + r')\.jsonl$', path.name)
        if match and match[1] != tid:
            fail_schema('stored rollout identity mismatch')
        roots = [self.home / n for n in ('sessions', 'archived_sessions')]
        if path.is_symlink() or not any(resolved.is_relative_to(root) for root in roots):
            fail_schema('rollout outside supported session directories')
        self.rollouts.setdefault(tid, set()).add(resolved)

    def load_projects(self):
        legacy = self.global_state.get('local-projects', {})
        if not isinstance(legacy, dict):
            fail_schema('unsupported local-projects registry')
        for pid, p in legacy.items():
            if not isinstance(p, dict) or p.get('id') != pid or not isinstance(p.get('name'), str) or not isinstance(p.get('rootPaths'), list) or not all(isinstance(x, str) for x in p['rootPaths']):
                fail_schema('unsupported legacy project')
        schema = self.schema.get('state', {})
        if 'projects' in schema:
            if 'project_roots' not in schema:
                fail_schema('project roots missing')
            db = self.dbs['state']
            mapping = self.global_state.get('app-server-project-id-by-legacy-project-id-by-host', {})
            if not isinstance(mapping, dict):
                fail_schema('invalid project migration map')
            aliases = mapping.get('local:' + str(self.home), {})
            if not isinstance(aliases, dict):
                fail_schema('invalid local project migration map')
            for row in db.execute('SELECT id,name FROM projects'):
                roots = [norm(r[0]) for r in db.execute('SELECT path FROM project_roots WHERE project_id=?', (row['id'],))]
                self.projects.append(dict(project_id=row['id'], name=row['name'], paths=roots,
                    legacy_ids=[k for k, v in aliases.items() if v == row['id']]))
        for pid, p in legacy.items():
            if not any(pid in r['legacy_ids'] for r in self.projects):
                self.projects.append(dict(project_id=pid, name=p['name'], paths=[norm(x) for x in p['rootPaths']], legacy_ids=[pid]))
        self.registry_supported = 'projects' in schema or 'local-projects' in self.global_state

    def resolve_thread(self, target):
        tid = None
        if target.startswith('codex:'):
            u = urlparse(target)
            if u.netloc != 'threads' or not UUID.fullmatch(u.path.lstrip('/')):
                raise Failure(3, 'invalid_input', detail='invalid Codex thread link')
            tid = u.path[1:]
        elif UUID.fullmatch(target):
            tid = target
        elif target.endswith('.jsonl'):
            path = Path(target).expanduser()
            candidates = [path.resolve()] if path.is_absolute() else [path.resolve(), (self.home / path).resolve(), (self.home / 'sessions' / path).resolve()]
            matches = [t for t, paths in self.rollouts.items() if paths.intersection(candidates)]
            tid = unique([{'thread_id': t} for t in matches])['thread_id']
        else:
            matches = []
            for t, rows in self.rows.items():
                for row in rows:
                    if row.get('display_title') == target:
                        matches.append(dict(thread_id=t, title=target, cwd=row.get('cwd'), host_id=row.get('host_id')))
            tid = unique(matches)['thread_id']
        tid = tid.lower()
        if tid not in self.rows and tid not in self.rollouts:
            raise Failure(1, 'not_found')
        return {tid}

    def resolve_project(self, target):
        pathlike = target.startswith(('/', '~', '.')) or '/' in target
        projects = self.projects
        if not self.registry_supported:
            paths = {norm(r['cwd']) for rows in self.rows.values() for r in rows if r.get('cwd')}
            projects = []
            for path in sorted(paths):
                pids = {r['project_id'] for rows in self.rows.values() for r in rows
                        if r.get('project_id') and r.get('cwd') and norm(r['cwd']) == path}
                for pid in sorted(pids) if pids else [None]:
                    projects.append(dict(project_id=pid, name=Path(path).name, paths=[path], legacy_ids=[]))
        return unique([p for p in projects if (norm(target) in p['paths'] if pathlike else p['name'] == target)])

    def project_threads(self, project):
        ids = {project['project_id'], *project['legacy_ids']} - {None}
        assignments = self.global_state.get('thread-project-assignments', {})
        if not isinstance(assignments, dict):
            fail_schema('invalid thread project assignments')
        result = set()
        for tid, rows in self.rows.items():
            assigned = assignments.get(tid, {})
            if not isinstance(assigned, dict):
                fail_schema('invalid thread project assignment')
            pids = {r['project_id'] for r in rows if r.get('project_id') and 'rollout_path' in r}
            if not pids:
                pids = {r['project_id'] for r in rows if r.get('project_id')}
            if not pids and assigned.get('projectKind') == 'local' and assigned.get('projectId'):
                pids.add(assigned['projectId'])
            if pids & ids or (not pids and any(r.get('cwd') and norm(r['cwd']) in project['paths'] for r in rows)):
                result.add(tid)
        return result

    def predicate(self, kind, table, tid):
        if table == 'thread_spawn_edges':
            return '(parent_thread_id=? OR child_thread_id=?)', [tid, tid]
        column = 'id' if table == 'threads' else 'thread_id'
        where, params = column + '=?', [tid]
        if kind == 'desktop':
            where += ' AND host_id IN (' + ','.join('?' for _ in self.local_hosts) + ')'
            params.extend(sorted(self.local_hosts))
        return where, params

    def delete_threads(self, ids, global_scope):
        counts = {}
        # Transactions are per DB; failures after any commit are explicitly partial.
        for kind, readonly in list(self.dbs.items()):
            readonly.close()
            db = sqlite3.connect((self.home / DB_PATHS[kind]).as_uri() + '?mode=rw', uri=True)
            self.dbs[kind] = db
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                changed = 0
                for table in TABLES[kind]:
                    if table not in self.schema[kind]:
                        continue
                    for tid in sorted(ids):
                        where, params = self.predicate(kind, table, tid)
                        changed += db.execute('DELETE FROM ' + table + ' WHERE ' + where, params).rowcount
                if kind == 'desktop':
                    if global_scope:
                        for table in ('local_thread_catalog_sync_state', 'local_thread_catalog_scan_checkpoints'):
                            if table not in self.schema[kind]:
                                continue
                            for host in self.local_hosts:
                                if table.endswith('checkpoints'):
                                    changed += db.execute('DELETE FROM ' + table + ' WHERE host_id=?', (host,)).rowcount
                                else:
                                    assignments = 'watermark_updated_at=NULL, initial_build_complete=0, observation_sequence=0'
                                    if 'last_full_reconciled_at' in self.schema[kind][table]:
                                        assignments += ', last_full_reconciled_at=NULL'
                                    changed += db.execute('UPDATE ' + table + ' SET ' + assignments + ' WHERE host_id=?', (host,)).rowcount
                    if changed and 'local_thread_catalog_metadata' in self.schema[kind]:
                        db.execute('UPDATE local_thread_catalog_metadata SET catalog_revision=catalog_revision+1 WHERE id=1')
                counts[kind] = changed
            self.committed |= bool(changed)
        deleted = 0
        for tid in sorted(ids):
            for path in sorted(self.rollouts.get(tid, ())):
                if path.exists():
                    path.unlink()
                    self.committed = True
                    deleted += 1
        for kind, db in self.dbs.items():
            for table in TABLES[kind]:
                if table not in self.schema[kind]:
                    continue
                for tid in ids:
                    where, params = self.predicate(kind, table, tid)
                    if db.execute('SELECT 1 FROM ' + table + ' WHERE ' + where, params).fetchone():
                        raise Failure(8, 'partial_failure', detail='thread state reappeared')
        if any(p.exists() for tid in ids for p in self.rollouts.get(tid, ())):
            raise Failure(8, 'partial_failure', detail='rollout reappeared')
        return deleted, counts

    def plan_global(self, ids, projects):
        state = json.loads(json.dumps(self.global_state))
        for key in ('thread-project-assignments', 'thread-project-membership-host-ids', 'electron-thread-read-state-v1'):
            if key in state:
                if not isinstance(state[key], dict):
                    fail_schema('unsupported state: ' + key)
                for tid in ids:
                    state[key].pop(tid, None)
        orders = state.get('sidebar-project-thread-orders', {})
        if not isinstance(orders, dict) or any(not isinstance(v, list) for v in orders.values()):
            fail_schema('unsupported sidebar project thread orders')
        for key, order in orders.items():
            orders[key] = [t for t in order if t not in ids]
        legacy_ids = {i for p in projects for i in p['legacy_ids']}
        for key in ('local-projects', 'project-appearances', 'sidebar-project-thread-orders'):
            if key in state:
                if not isinstance(state[key], dict):
                    fail_schema('unsupported state: ' + key)
                for pid in legacy_ids:
                    state[key].pop(pid, None)
        if legacy_ids:
            if 'project-order' in state:
                if not isinstance(state['project-order'], list):
                    fail_schema('unsupported project order')
                state['project-order'] = [p for p in state['project-order'] if p not in legacy_ids]
            selected = state.get('selected-project')
            if isinstance(selected, dict) and selected.get('type') == 'local' and selected.get('projectId') in legacy_ids:
                state.pop('selected-project')
            mapping = state.get('app-server-project-id-by-legacy-project-id-by-host', {})
            for pid in legacy_ids:
                mapping.get('local:' + str(self.home), {}).pop(pid, None)
        return state

    def delete_projects(self, projects):
        if 'projects' not in self.schema.get('state', {}):
            return
        db = self.dbs['state']
        with db:
            for p in projects:
                for table in ('project_idempotency_keys', 'project_roots', 'projects'):
                    if table in self.schema['state']:
                        column = 'id' if table == 'projects' else 'project_id'
                        db.execute('DELETE FROM ' + table + ' WHERE ' + column + '=?', (p['project_id'],))
        self.committed |= bool(projects)
        for p in projects:
            for table in ('projects', 'project_roots', 'project_idempotency_keys'):
                if table not in self.schema['state']:
                    continue
                column = 'id' if table == 'projects' else 'project_id'
                if db.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + '=?', (p['project_id'],)).fetchone():
                    raise Failure(8, 'partial_failure', detail='project state reappeared')

    def write_global(self, state):
        if state == self.global_state:
            return
        if self.global_path.read_bytes() != self.global_bytes:
            raise Failure(8, 'partial_failure', detail='global state changed concurrently; retry with Codex closed')
        fd, temp = tempfile.mkstemp(prefix='.cleanup-', dir=self.home)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream, ensure_ascii=True, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, self.global_path.stat().st_mode & 0o777)
            os.replace(temp, self.global_path)
            self.committed = True
            if json.loads(self.global_path.read_bytes()) != state:
                raise Failure(8, 'partial_failure', detail='global state verification failed')
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure(3, 'invalid_input', detail=message)


def run(argv=None):
    store = None
    try:
        parser = Parser(description=__doc__)
        sub = parser.add_subparsers(dest='scope', required=True, parser_class=Parser)
        for scope in ('thread', 'project', 'all'):
            cmd = sub.add_parser(scope)
            if scope != 'all':
                cmd.add_argument('target')
            cmd.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser())
            cmd.add_argument('--dry-run', action='store_true')
            if scope != 'thread':
                group = cmd.add_mutually_exclusive_group()
                group.add_argument('--keep-projects', action='store_true')
                group.add_argument('--delete-projects', action='store_true')
        args = parser.parse_args(argv)
        store = Store(args.codex_home.expanduser())
        store.load()
        projects = []
        if args.scope == 'thread':
            ids = store.resolve_thread(args.target)
        elif args.scope == 'project':
            projects = [store.resolve_project(args.target)]
            ids = store.project_threads(projects[0])
        else:
            ids = set(store.rows) | set(store.rollouts)
            projects = store.projects
        deleting = projects if getattr(args, 'delete_projects', False) else []
        if getattr(args, 'delete_projects', False) and (not store.registry_supported or store.unknown_project_tables):
            raise Failure(7, 'unsupported_project_deletion')
        state = store.plan_global(ids, deleting)
        result = dict(scope=args.scope, projects_deleted=0)
        if args.scope == 'project':
            result.update(project=projects[0]['name'], paths=projects[0]['paths'])
        if args.dry_run:
            return 0, dict(result, status='dry_run', thread_ids=sorted(ids), threads=len(ids), projects_to_delete=len(deleting))
        rollouts, counts = store.delete_threads(ids, args.scope == 'all')
        store.delete_projects(deleting)
        store.write_global(state)
        return 0, dict(result, status='deleted', threads_deleted=len(ids), rollouts_deleted=rollouts,
                       rollout='removed' if rollouts else 'already_missing', rows_deleted=counts,
                       projects_deleted=len(deleting))
    except Failure as exc:
        if store and store.committed:
            return 8, dict(exc.result, status='partial_failure')
        return exc.code, exc.result
    except (TypeError, ValueError, KeyError) as exc:
        code = 8 if store and store.committed else 6
        return code, dict(status='partial_failure' if code == 8 else 'unsupported_schema', detail=str(exc))
    except (sqlite3.Error, OSError) as exc:
        code = 8 if store and store.committed else (4 if isinstance(exc, sqlite3.Error) else 5)
        return code, dict(status={4: 'database_error', 5: 'filesystem_error', 8: 'partial_failure'}[code], detail=str(exc))
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    code, result = run()
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    sys.exit(code)
