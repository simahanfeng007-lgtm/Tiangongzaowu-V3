"""Initial CMD directory translation: bound path equivalence, not command rewriting."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from omni_body_skill.tools import sandbox_runtime as sandbox


class InitialCmdDirectoryTests(unittest.TestCase):
    cmd = r'C:\Windows\System32\cmd.exe'
    workspace = r'C:\private\workspace'

    def render(self, command, cwd=None, root=None):
        return sandbox._prepare_windows_cmd_initial_directory(
            command, cwd=cwd or self.workspace,
            workspace=root or self.workspace, comspec=self.cmd,
        )

    def raw(self, body):
        return self.cmd + ' /d /s /c "' + body + '"'

    def test_initial_absolute_cd_becomes_equivalent_relative_target(self):
        suffix = ' && echo sandbox-ok>result.txt'
        original = self.raw('cd /d "' + self.workspace + r'\project"' + suffix)
        self.assertEqual(self.render(original), self.raw('cd /d "project"' + suffix))

    def test_explicit_child_cwd_uses_parent_relative_target(self):
        original = self.raw('cd /d "' + self.workspace + r'\sibling" && exit 7')
        self.assertEqual(self.render(original, cwd=self.workspace + r'\child'),
                         self.raw(r'cd /d "..\sibling" && exit 7'))

    def test_cwd_target_is_dot_without_changing_switches_or_suffix(self):
        original = self.raw('  @ChDiR /D "' + self.workspace + '" || exit 19')
        self.assertEqual(self.render(original), self.raw('  @ChDiR /D "." || exit 19'))

    def test_unicode_space_target_preserves_the_remainder_exactly(self):
        suffix = ' && echo "中文 & quote" > "a.txt" & exit 7'
        original = self.raw('cd /d "' + self.workspace + r'\项目 甲"' + suffix)
        self.assertEqual(self.render(original), self.raw('cd /d "项目 甲"' + suffix))

    def test_unquoted_outer_payload_and_quoted_executable(self):
        original = '"' + self.cmd + '" /d /c cd "' + self.workspace + r'\project" && exit 0'
        expected = '"' + self.cmd + '" /d /c cd "project" && exit 0'
        self.assertEqual(self.render(original), expected)

    def test_relative_cd_or_later_cd_is_not_reinterpreted(self):
        cases = ['cd /d project && echo ok',
                 'echo start & cd /d "' + self.workspace + r'\project"',
                 '(cd /d "' + self.workspace + r'\project")',
                 'call cd /d "' + self.workspace + r'\project"',
                 'cd..', 'echo cd /d "' + self.workspace + '"']
        for body in cases:
            with self.subTest(body=body):
                command = self.raw(body)
                self.assertEqual(self.render(command), command)

    def test_outside_root_sibling_prefix_and_other_drive_stay_unchanged(self):
        for path in (r'C:\private\outside', self.workspace + r'_other\file',
                     self.workspace + r'\..\outside', r'D:\private\workspace\file'):
            with self.subTest(path=path):
                command = self.raw('cd /d "' + path + '" && echo forbidden')
                self.assertEqual(self.render(command), command)

    def test_unbound_or_non_dos_directory_never_translates(self):
        command = self.raw('cd /d "' + self.workspace + r'\project"')
        for cwd in (r'C:\private\outside', r'workspace', r'\\server\share\path',
                    '\\\\?\\' + self.workspace):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.render(command, cwd=cwd), command)

    def test_shell_metacharacters_and_expansions_are_not_interpreted(self):
        for fragment in ('%NAME%', '!NAME!', 'a^b', 'a&b', 'a|b', 'a<b', 'a>b',
                         'a*b', 'a?b', 'a\nb', 'a\rb', 'a\x00b'):
            command = self.raw('cd /d "' + self.workspace + '\\' + fragment + '" && exit 0')
            with self.subTest(fragment=fragment):
                self.assertEqual(self.render(command), command)

    def test_unrecognized_executable_flags_or_list_preserve_input(self):
        original = self.raw('cd /d "' + self.workspace + r'\project"')
        variants = [original.replace(self.cmd, r'C:\other\cmd.exe'),
                    original.replace(' /d /s /c ', ' /s /c '),
                    original.replace(' /d /s /c ', ' /d /v:on /c '),
                    original.replace(' /d /s /c ', ' /d /d /c '),
                    original.replace(' /d /s /c ', ' /d /s /k '),
                    [self.cmd, '/d', '/s', '/c', 'cd /d "' + self.workspace + r'\project"']]
        for command in variants:
            with self.subTest(command=command):
                self.assertEqual(self.render(command), command)

    def test_first_command_suffix_must_be_a_supported_separator_or_end(self):
        for tail in (' extra', ' >out.txt', ' | more', ')', ' ^& echo ok'):
            command = self.raw('cd /d "' + self.workspace + r'\project"' + tail)
            with self.subTest(tail=tail):
                self.assertEqual(self.render(command), command)

    def test_case_and_forward_slash_alias_are_dos_equivalent(self):
        command = self.raw('CD /d "c:/PRIVATE/WORKSPACE/Project" && exit 0')
        self.assertEqual(self.render(command), self.raw('CD /d "Project" && exit 0'))


@unittest.skipUnless(os.name == 'nt', 'requires native Windows AppContainer')
class NativeCmdDirectoryTests(unittest.TestCase):
    def test_native_absolute_cd_readback_failure_and_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / '工作 区'
            child = workspace / 'child'
            target = workspace / '项目 甲'
            child.mkdir(parents=True)
            target.mkdir()
            outside = root / 'parent-secret.txt'
            outside.write_text('must-not-leak', encoding='utf-8')
            runner = sandbox.SandboxRunner(workspace, root/'state', root/'trash',
                sandbox.SandboxLimits(timeout_seconds=20))
            cmd = subprocess.list2cmdline([os.environ.get('COMSPEC') or 'cmd.exe'])
            def run(body, name):
                result = runner.run(cmd + ' /d /s /c "' + body + '"', cwd=child,
                                    op_id='native-cmd-p12-' + name)
                self.assertEqual(result['containment'], 'windows-appcontainer', result)
                self.assertFalse(Path(result['sandbox_root']).exists())
                return result
            with mock.patch.dict(os.environ, {'TIANGONG_SANDBOX_COMPAT':'0', 'OPENAI_API_KEY':'secret-sentinel'}):
                written = run(f'cd /d "{target}" && echo sandbox-ok>result.txt', 'write')
                self.assertEqual(written['returncode'], 0, written)
                self.assertEqual((target/'result.txt').read_text(encoding='utf-8').strip(), 'sandbox-ok')
                self.assertEqual([p.replace('\\','/') for p in written['changed_files']], ['项目 甲/result.txt'])
                self.assertFalse((child/'result.txt').exists())
                failed = run(f'cd /d "{target}" && exit 7', 'exit')
                self.assertEqual(failed['returncode'], 7, failed)
                self.assertFalse(failed['ok'])
                missing = run(f'cd /d "{workspace / "missing"}" && echo bad>must-not-exist.txt', 'missing')
                self.assertNotEqual(missing['returncode'], 0, missing)
                self.assertEqual(missing['changed_files'], [], missing)
                boundary = run(f'cd /d "{target}" && if defined OPENAI_API_KEY (exit 91) else (type "{outside}")', 'boundary')
                self.assertNotEqual(boundary['returncode'], 0, boundary)
                self.assertNotEqual(boundary['returncode'], 91, boundary)
                self.assertNotIn('must-not-leak', boundary['stdout'])
                self.assertEqual(outside.read_text(encoding='utf-8'), 'must-not-leak')
                self.assertEqual(boundary['changed_files'], [], boundary)


if __name__ == '__main__':
    unittest.main()
