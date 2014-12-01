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

import mock

from neutron_powervm.plugins.ibm.agent.powervm import utils

from neutron_powervm.tests.unit.ibm.powervm import base

from pypvm.tests.wrappers.util.pvmhttp import load_pvm_resp
from pypvm.wrappers import network as w_net

NET_BR_FILE = 'fake_network_bridge.txt'


class UtilsTest(base.BasePVMTestCase):
    '''
    Tests the utility functions for the Shared Ethernet Adapter Logic.
    '''

    def setUp(self):
        super(UtilsTest, self).setUp()

        self.net_br_resp = load_pvm_resp(NET_BR_FILE).get_response()

    @mock.patch('pypvm.adapter.Session')
    @mock.patch('pypvm.adapter.Adapter')
    def test_list_bridges(self, fake_adapter, fake_session):
        '''
        Test that we can load the bridges in properly.
        '''
        # Set up the response data to the read
        attrs = {'read.return_value': self.net_br_resp}
        fake_adapter.configure_mock(**attrs)
        test_utils = utils.NetworkBridgeUtils(None, None, None, None)
        test_utils.adapter = fake_adapter

        # Assert that two are read in
        bridges = test_utils.list_bridges()
        self.assertEqual(2, len(bridges))
        self.assertTrue(isinstance(bridges[0], w_net.NetworkBridge))
