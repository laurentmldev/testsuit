# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.

"""Issues of issue trackers (GitLab, GitHub, Jira, Redmine, or a plugin's) bound to test results and requirements.

- ``model``: an issue, the errors;
- ``backend``: the plugin API (``IssueBackend``, ``register_issue_backend()``) and the HTTP shared by the built-ins;
- ``gitlab``, ``github``, ``jira``: the built-in backends;
- ``sources``: the issues file (``issues.yml`` at the root of the tests repository) and its sources;
- ``issues``: issues about test results (pre-filled, found again by their fingerprint) and their requirements.
"""
from testsuit.issues.backend import (IssueBackend, describe_issue_backends, get_issue_backends,
                                      register_issue_backend, unregister_issue_backend)
from testsuit.issues.model import CLOSED, OPEN, Issue, IssueError
from testsuit.issues import github as _github  # noqa: F401,E402  (registers the built-in backends)
from testsuit.issues import gitlab as _gitlab  # noqa: F401,E402
from testsuit.issues import jira as _jira  # noqa: F401,E402
from testsuit.issues import redmine as _redmine  # noqa: F401,E402

__all__ = ["CLOSED", "OPEN", "Issue", "IssueBackend", "IssueError", "describe_issue_backends", "get_issue_backends",
           "register_issue_backend", "unregister_issue_backend"]
