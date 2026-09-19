"""P12 R1A developer-contract tests; fixtures do not prove runtime parity."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/audit-p12-retirement-map.py'
spec = importlib.util.spec_from_file_location('p12_map_test_target', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture_texts():
    texts = {}
    for path, symbol, _ in m.ANCHORS.values():
        if symbol:
            texts[path] = texts.get(path, '') + f'def {symbol}(*args, **kwargs):\n    raise RuntimeError("MUST_NOT_IMPORT_PRODUCT")\n\n'
        elif path != m.INDEX_PATH:
            texts[path] = 'raise RuntimeError("MUST_NOT_IMPORT_PRODUCT")\n'
    texts[m.INDEX_PATH] = json.dumps({
        'schema': 'tiangong.v3.omni_body.skill_router_index.v1', 'skill_count': 1,
        'skills': [{'id': 'fixture_v1', 'file': 'deliverable_skills/example.md',
                    'starter_actions': ['file.read'], 'production_actions': ['file.read', 'file.write']}]})
    texts[m.SKILL_ROOT + 'deliverable_skills/example.md'] = 'Fixture only.\n'
    return texts


def native_rows(texts):
    return {path: {'path': path, 'mode': '100644', 'kind': 'blob', 'size': len(text.encode()),
                   'oid': hashlib.sha1(b'blob ' + str(len(text.encode())).encode() + b'\0' + text.encode()).hexdigest()}
            for path, text in texts.items()}


class ConsumerMapUnitTests(unittest.TestCase):
    def setUp(self):
        self.texts = fixture_texts()
        self.rows = native_rows(self.texts)
        self.base = copy.deepcopy(self.rows)
        self.pairs = m.strict_pairs

    def build(self):
        return m.build_map(self.texts, self.rows, self.base, self.pairs)

    def refs(self, code, path='src/total_gateway/consumer.py'):
        return m.syntax_references(path, ast.parse(code))

    def test_all_seventeen_anchors_and_five_surfaces_resolve(self):
        report = self.build()
        self.assertEqual(len(report['anchors']), 17)
        self.assertEqual(len(report['surfaces']), 5)
        self.assertFalse(report['complete_call_graph'])
        self.assertFalse(report['full_consumer_review_completed'])

    def test_mixed_module_keeps_dynamic_manifest_functions(self):
        anchors = self.build()['anchors']
        self.assertEqual(anchors['static.selector']['path'], anchors['shared.execution_manifest']['path'])
        self.assertEqual(anchors['shared.execution_manifest']['disposition'], 'KEEP_DYNAMIC_DEPENDENCY')
        self.assertEqual(anchors['router.module']['disposition'], 'MIXED_MODULE_KEEP')

    def test_never_upgrades_replacement_presence_to_parity(self):
        report = self.build()
        for row in report['surfaces'].values():
            self.assertFalse(row['capability_parity_proven'])
            self.assertFalse(row['replacement_wiring_proven'])
        self.assertTrue(all(not item['retirement_authorized'] for item in report['anchors'].values()))
        self.assertFalse(report['skill_migration_matrix'][0]['replacement_behavior_proven'])

    def test_index_actions_are_deduplicated_without_losing_requirements(self):
        row = self.build()['skill_migration_matrix'][0]
        self.assertEqual(row['required_actions'], ['file.read', 'file.write'])
        self.assertTrue(row['source_preserved'])
        self.assertEqual(row['migration_status'], 'REQUIRES_TASK_PARITY')

    def test_missing_anchor_is_not_empty_success(self):
        del self.texts[m.ANCHORS['dynamic.plan'][0]]
        with self.assertRaises(m.MapError): self.build()

    def test_imported_symbol_does_not_fake_definition(self):
        path, symbol, _ = m.ANCHORS['dynamic.plan']
        self.texts[path] = 'from elsewhere import ' + symbol
        with self.assertRaises(m.MapError): self.build()

    def test_duplicate_definition_rejected(self):
        path, symbol, _ = m.ANCHORS['dynamic.plan']
        self.texts[path] += f'\ndef {symbol}(): pass\n'
        with self.assertRaises(m.MapError): self.build()

    def test_unrelated_parse_failures_are_disclosed_not_silently_dropped(self):
        self.texts['src/unknown.py'] = 'def broken('\
            '\n'
        report = self.build()
        self.assertEqual(report['unparsed_python_files'], [{'path': 'src/unknown.py', 'reason': 'AST_PARSE_UNAVAILABLE'}])
        self.assertFalse(report['complete_call_graph'])

    def test_required_parse_failure_blocks_anchor_resolution(self):
        self.texts[m.ANCHORS['dynamic.plan'][0]] = 'def broken('
        with self.assertRaises(m.MapError): self.build()

    def test_changed_anchor_blocks_preservation(self):
        self.rows[m.ANCHORS['dynamic.plan'][0]]['oid'] = 'a' * 40
        with self.assertRaises(m.MapError): self.build()

    def test_from_import_alias_and_call_are_candidates_not_execution(self):
        report = self.refs('from .skill_selection import compile_composition_execution_manifest as c\nc()\n')
        self.assertEqual([x['kind'] for x in report['references']], ['IMPORT_BINDING', 'CALL_CANDIDATE_SCOPE_UNRESOLVED'])
        self.assertEqual({x['anchor'] for x in report['references']}, {'shared.execution_manifest'})

    def test_module_import_alias_and_unaliased_dotted_import(self):
        for code in ('import total_gateway.skill_selection as s\ns.compile_composition_execution_manifest()\n',
                     'import total_gateway.skill_selection\ntotal_gateway.skill_selection.compile_composition_execution_manifest()\n',
                     'from total_gateway import skill_selection as s\ns.compile_composition_execution_manifest()\n'):
            with self.subTest(code=code):
                self.assertEqual(self.refs(code)['references'][0]['anchor'], 'shared.execution_manifest')

    def test_unrelated_same_named_symbol_is_not_a_known_import(self):
        self.assertEqual(self.refs('from unrelated import compile_composition_execution_manifest\ncompile_composition_execution_manifest()')['references'], [])

    def test_shadowed_name_is_explicitly_unresolved(self):
        report = self.refs('from total_gateway.skill_selection import SkillCatalog\ndef f(SkillCatalog):\n    return SkillCatalog()\n')
        self.assertIn('CALL_CANDIDATE_SCOPE_UNRESOLVED', {x['kind'] for x in report['references']})

    def test_relative_import_root_and_package_init_resolution(self):
        node = ast.parse('from ..skill_selection import SkillCatalog').body[0]
        self.assertEqual(m.import_origin('src/total_gateway/sub/consumer.py', node), 'total_gateway.skill_selection')
        node = ast.parse('from .skill_selection import SkillCatalog').body[0]
        self.assertEqual(m.import_origin('src/total_gateway/__init__.py', node), 'total_gateway.skill_selection')
        self.assertIsNone(m.import_origin('tests/consumer.py', node))

    def test_star_import_and_reflection_remain_unresolved(self):
        report = self.refs('from total_gateway.skill_selection import *\ngetattr(obj, "SECRET_TEXT")()\n')
        self.assertEqual(report['dynamic_site_count'], 2)
        self.assertNotIn('SECRET_TEXT', json.dumps(report))

    def test_reference_truncation_disclosed(self):
        report = self.refs('from total_gateway.skill_selection import SkillCatalog\n' + 'SkillCatalog()\n' * 300)
        self.assertEqual(report['reference_count'], 301)
        self.assertEqual(len(report['references']), 250)
        self.assertTrue(report['references_truncated'])

    def test_duplicate_json_key_bool_count_and_nan_rejected(self):
        original = self.texts[m.INDEX_PATH]
        for bad in (original.replace('"skill_count": 1', '"skill_count": 1, "skill_count": 1'),
                    original.replace('"skill_count": 1', '"skill_count": true'),
                    original.replace('"skill_count": 1', '"skill_count": NaN')):
            self.texts[m.INDEX_PATH] = bad
            with self.subTest(bad=bad), self.assertRaises(m.MapError): self.build()

    def test_unsafe_skill_paths_rejected(self):
        data = json.loads(self.texts[m.INDEX_PATH])
        for name in ('../secret.md', '/secret.md', 'deliverable_skills/../x.md', 'deliverable_skills\\x.md',
                     'deliverable_skills//x.md', 'deliverable_skills/C:x.md', 'deliverable_skills/x.md\x00'):
            data['skills'][0]['file'] = name
            self.texts[m.INDEX_PATH] = json.dumps(data)
            with self.subTest(name=name), self.assertRaises(m.MapError): self.build()

    def test_missing_changed_symlink_corpus_rejected(self):
        path = m.SKILL_ROOT + 'deliverable_skills/example.md'
        original = copy.deepcopy(self.rows[path])
        for mutation in ({'oid': 'b' * 40}, {'mode': '120000'}):
            self.rows[path] = {**original, **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(m.MapError): self.build()
        self.rows.pop(path)
        with self.assertRaises(m.MapError): self.build()

    def test_duplicate_skill_identity_rejected(self):
        data = json.loads(self.texts[m.INDEX_PATH])
        data['skills'].append(copy.deepcopy(data['skills'][0]))
        data['skill_count'] = 2
        self.texts[m.INDEX_PATH] = json.dumps(data)
        with self.assertRaises(m.MapError): self.build()

    def test_index_action_type_and_empty_corpus_rejected(self):
        data = json.loads(self.texts[m.INDEX_PATH])
        data['skills'][0]['starter_actions'] = 'file.read'
        self.texts[m.INDEX_PATH] = json.dumps(data)
        with self.assertRaises(m.MapError): self.build()
        data['skills'] = []
        data['skill_count'] = 0
        self.texts[m.INDEX_PATH] = json.dumps(data)
        with self.assertRaises(m.MapError): self.build()

    def test_deterministic_map(self):
        self.assertEqual(self.build(), self.build())


class ConsumerMapGitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'P12 Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        for path, text in fixture_texts().items(): self.put(path, text)
        self.put(m.R0_PATH, (ROOT / m.R0_PATH).read_bytes())
        self.put(m.WORKFLOW, 'name: fixture baseline\n')
        self.base = self.commit('R0 synthetic baseline')
        self.put(m.SELF_PATH, SCRIPT.read_bytes())
        self.put(m.WORKFLOW, 'name: fixture R1\n')
        self.head = self.commit('R1 observer')

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, env=env).stdout

    def put(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode() if isinstance(text, str) else text)

    def commit(self, message):
        self.git('add', '-A'); self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def audit(self): return m.audit(self.repo, self.head, self.base)

    def test_git_snapshot_positive_and_non_authorization_flags(self):
        report = self.audit()
        self.assertTrue(report['mapping_gate_passed'])
        self.assertTrue(report['source_preservation_proven'])
        for key in ('p11_exit_proven', 'p12_authorized', 'retirement_authorized', 'production_zero_usage_proven', 'capability_parity_proven'):
            self.assertIs(report[key], False)

    def test_wrong_head_and_dirty_tracked_tree_rejected(self):
        with self.assertRaises(ValueError): m.audit(self.repo, self.base, self.base)
        self.put('src/total_gateway/skill_selection.py', 'changed = True\n')
        with self.assertRaises(ValueError): self.audit()

    def test_non_anchor_product_change_cannot_hide_in_scope(self):
        self.put('src/second_runtime.py', 'pass\n')
        self.head = self.commit('out of scope')
        report = self.audit()
        self.assertFalse(report['mapping_gate_passed'])
        self.assertFalse(report['source_preservation_proven'])
        self.assertIn({'path': 'src/second_runtime.py', 'status': 'A'}, report['prohibited_changes'])

    def test_original_r0_script_change_is_rejected(self):
        self.put(m.R0_PATH, '# tampered\n')
        self.head = self.commit('tamper R0')
        with self.assertRaises(ValueError): self.audit()

    def test_observer_bytes_cannot_be_swapped(self):
        self.put(m.SELF_PATH, '# tampered\n')
        self.head = self.commit('tamper R1')
        with self.assertRaisesRegex(m.MapError, 'observer'): self.audit()

    def test_report_hash_recomputable_and_snapshot_unchanged(self):
        before = self.git('status', '--porcelain')
        report = self.audit()
        digest = report.pop('report_sha256')
        self.assertEqual(digest, hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        self.assertEqual(before, self.git('status', '--porcelain'))
        self.assertEqual(self.head, self.git('rev-parse', 'HEAD').decode().strip())

    def test_cli_success_error_and_scope_failure_codes(self):
        cmd = [os.sys.executable, str(SCRIPT), '--repo', str(self.repo), '--expected-head', self.head, '--baseline-head', self.base]
        result = subprocess.run(cmd, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['retirement_authorized'])
        self.put('src/outside.py', 'pass\n'); self.head = self.commit('outside scope')
        cmd[5] = self.head
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
        cmd[5] = self.base
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 1)


if __name__ == '__main__':
    unittest.main()
