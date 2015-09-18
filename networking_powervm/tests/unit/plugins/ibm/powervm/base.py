# Copyright 2014 IBM Corp.
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from neutron.tests import base

import os
import shutil


class BasePVMTestCase(base.BaseTestCase):
    """The base PowerVM Test case."""

    def setUp(self):
        super(BasePVMTestCase, self).setUp()

        # We need to try to copy over the policy.json.  Some neutron
        # modules load it, but since we start in a different location
        # we lose its context...  Copying will bring it in.
        policy_json, to = self._get_policy_paths()
        if not os.path.exists(to):
            shutil.copyfile(policy_json, to)

    def tearDown(self):
        super(BasePVMTestCase, self).tearDown()

        # Remove the policy now that it is no longer used.
        policy_json, to = self._get_policy_paths()
        if os.path.exists(to):
            os.remove(to)

    def _get_policy_paths(self):
        """
        Returns the source policy path from neutron and a target path
        to store the file in temporarily for the tests.
        """
        # Start with the source path.
        tests = os.path.split(base.__file__)[0]
        neutron_src = os.path.split(tests)[0]
        neutron = os.path.split(neutron_src)[0]
        policy_json = os.path.join(neutron, 'etc/policy.json')

        # Get the copy to path
        home_path = os.path.abspath(os.path.expanduser('~'))
        to = os.path.join(home_path, 'policy.json')

        # return the two
        return policy_json, to
