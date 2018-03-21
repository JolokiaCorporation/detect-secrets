#!/usr/bin/python
from __future__ import absolute_import

import mock
import pytest

from detect_secrets.core import baseline
from detect_secrets.core.baseline import apply_baseline_filter
from detect_secrets.core.potential_secret import PotentialSecret
from detect_secrets.plugins.high_entropy_strings import Base64HighEntropyString
from detect_secrets.plugins.high_entropy_strings import HexHighEntropyString
from tests.util.factories import secrets_collection_factory
from tests.util.mock_util import mock_subprocess
from tests.util.mock_util import SubprocessMock


class TestApplyBaselineFilter(object):

    def test_nothing_new(self):
        # We want a secret, but just a default secret (no overriding parameters)
        new_findings = secrets_collection_factory([{}])
        baseline = secrets_collection_factory([{}])

        results = apply_baseline_filter(new_findings, baseline, ['filename'])

        # No expected results, because everything filtered out by baseline
        assert len(results.data) == 0

        # Make sure that baseline didn't get modified either
        assert len(baseline.data) == 1
        assert next(iter(baseline.data['filename'])).lineno == 1

    def test_new_file(self):
        new_findings = secrets_collection_factory([
            {
                'filename': 'filename1',
            }
        ])
        baseline = secrets_collection_factory([
            {
                'filename': 'filename2',
            }
        ])

        backup_baseline = baseline.data.copy()
        results = apply_baseline_filter(new_findings, baseline, ['filename1', 'filename2'])

        assert len(results.data) == 1
        assert 'filename1' in results.data
        assert baseline.data == backup_baseline

    def test_new_file_excluded(self):
        new_findings = secrets_collection_factory([
            {
                'filename': 'filename1',
            },
            {
                'filename': 'filename2',
            }
        ])
        baseline = secrets_collection_factory([
            {
                'filename': 'filename3',
            }
        ])

        backup_baseline = baseline.data.copy()
        baseline.exclude_regex = 'filename1'
        results = apply_baseline_filter(new_findings, baseline, ['filename1', 'filename2'])

        assert len(results.data) == 1
        assert 'filename1' not in results.data
        assert baseline.data == backup_baseline

    def test_new_secret_line_old_file(self):
        """Same file, new line with potential secret"""
        new_findings = secrets_collection_factory([
            {
                'secret': 'secret1',
                'lineno': 1,
            }
        ])
        baseline = secrets_collection_factory([
            {
                'secret': 'secret2',
                'lineno': 2,
            }
        ])

        backup_baseline = baseline.data.copy()
        results = apply_baseline_filter(new_findings, baseline, ['filename'])

        assert len(results.data['filename']) == 1
        secretA = PotentialSecret('type', 'filename', 1, 'secret1')
        assert results.data['filename'][secretA].secret_hash == PotentialSecret.hash_secret('secret1')
        assert baseline.data == backup_baseline

    def test_rolled_creds(self):
        """Same line, different secret"""
        new_findings = secrets_collection_factory([
            {
                'secret': 'secret_new',
            }
        ])
        baseline = secrets_collection_factory([
            {
                'secret': 'secret',
            }
        ])

        backup_baseline = baseline.data.copy()
        results = apply_baseline_filter(new_findings, baseline, ['filename'])

        assert len(results.data['filename']) == 1

        secretA = PotentialSecret('type', 'filename', 1, 'secret_new')
        assert results.data['filename'][secretA].secret_hash == PotentialSecret.hash_secret('secret_new')
        assert baseline.data == backup_baseline

    def test_deleted_secret(self):
        new_findings = secrets_collection_factory([
            {
                'secret': 'secret',
                'lineno': 2,
            }
        ])
        baseline = secrets_collection_factory([
            {
                'secret': 'deleted_secret',
                'lineno': 1,
            },
            {
                'secret': 'secret',
                'lineno': 2,
            }
        ])

        results = apply_baseline_filter(new_findings, baseline, ['filename'])

        # Since hotdog doesn't appear in new_findings, it should be removed.
        assert len(results.data) == 0
        assert len(baseline.data) == 1
        assert next(iter(baseline.data['filename'])).lineno == 2

    def test_deleted_secret_file(self):
        new_findings = secrets_collection_factory()
        baseline = secrets_collection_factory()

        results = apply_baseline_filter(new_findings, baseline, ['filename', 'non_relevant_file'])

        # No results, but baseline should be modified.
        assert len(results.data) == 0
        assert len(baseline.data) == 0

    def test_same_secret_new_location(self):
        new_findings = secrets_collection_factory([
            {
                'lineno': 1,
            }
        ])
        baseline = secrets_collection_factory([
            {
                'lineno': 2,
            },
        ])

        results = apply_baseline_filter(new_findings, baseline, ['filename'])

        # No results, but baseline should be modified with new line location.
        assert len(results.data) == 0
        assert len(baseline.data) == 1
        assert next(iter(baseline.data['filename'])).lineno == 1


class TestInitializeBaseline(object):

    def setup(self):
        self.plugins = (
            Base64HighEntropyString(4.5),
            HexHighEntropyString(3),
        )

    def get_results(self, rootdir='./test_data/files', exclude_regex=None):
        return baseline.initialize(
            self.plugins,
            rootdir=rootdir,
            exclude_regex=exclude_regex,
        ).json()

    @pytest.mark.parametrize(
        'rootdir',
        [
            './test_data/files',

            # Test relative paths
            'test_data/../test_data/files/tmp/..',
        ]
    )
    def test_basic_usage(self, rootdir):
        results = self.get_results(rootdir=rootdir)

        assert len(results.keys()) == 2
        assert len(results['test_data/files/file_with_secrets.py']) == 1
        assert len(results['test_data/files/tmp/file_with_secrets.py']) == 2

    def test_exclude_regex(self):
        results = self.get_results(exclude_regex='tmp*')

        assert len(results.keys()) == 1
        assert 'test_data/files/file_with_secrets.py' in results

    def test_exclude_regex_at_root_level(self):
        results = self.get_results(exclude_regex='file_with_secrets.py')

        # All files_with_secrets.py should be ignored, both at the root
        # level, and the nested file in tmp.
        assert not results

    @mock.patch('detect_secrets.core.baseline.subprocess.check_output')
    def test_no_files_in_git_repo(self, mock_subprocess_obj):
        mock_subprocess_obj.side_effect = mock_subprocess((
            SubprocessMock(
                expected_input='git ls-files will_be_mocked',
                should_throw_exception=True,
                mocked_output='',
            ),
        ))

        results = self.get_results(rootdir='will_be_mocked')
        assert not results