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

import os

from pypowervm.tests.wrappers.util import pvmhttp
from pypowervm.wrappers import network as w_net

NET_BR_FILE = 'fake_network_bridge.txt'
VM_FILE = 'fake_lpar_feed.txt'
CNA_FILE = 'fake_network_bridge.txt'


class UtilsTest(base.BasePVMTestCase):
    '''
    Tests the utility functions for the Shared Ethernet Adapter Logic.
    '''

    def setUp(self):
        super(UtilsTest, self).setUp()

        # Find directory for response files
        data_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(data_dir, 'data')

        def resp(file_name):
            file_path = os.path.join(data_dir, file_name)
            return pvmhttp.load_pvm_resp(file_path).get_response()

        self.net_br_resp = resp(NET_BR_FILE)
        self.vm_feed_resp = resp(VM_FILE)
        self.cna_resp = resp(CNA_FILE)

    def __build_fake_utils(self, fake_adapter, fake_session, feed):
        '''
        Helper method to make the mock adapter.
        '''
        # Sets the feed to be the response on the adapter for a single read
        attrs = {'read.return_value': feed}
        fake_adapter.configure_mock(**attrs)
        test_utils = utils.NetworkBridgeUtils(None, None, None, None)
        test_utils.adapter = fake_adapter
        return test_utils

    @mock.patch('pypowervm.adapter.Session')
    @mock.patch('pypowervm.adapter.Adapter')
    def test_list_bridges(self, fake_adapter, fake_session):
        '''
        Test that we can load the bridges in properly.
        '''
        test_utils = self.__build_fake_utils(fake_adapter, fake_session,
                self.net_br_resp)

        # Assert that two are read in
        bridges = test_utils.list_bridges()
        self.assertEqual(2, len(bridges))
        self.assertTrue(isinstance(bridges[0], w_net.NetworkBridge))

    @mock.patch('pypowervm.adapter.Session')
    @mock.patch('pypowervm.adapter.Adapter')
    def test_list_vm_entries(self, fake_adapter, fake_session):
        '''
        Validates that VMs can be iterated on properly.
        '''
        test_utils = self.__build_fake_utils(fake_adapter, fake_session,
                self.vm_feed_resp)

        # List the VMs and make some assertions
        vm_list = test_utils._list_vm_entries()
        self.assertEqual(17, len(vm_list))
        for vm in vm_list:
            self.assertTrue(len(vm.get_cna_uris()) > 0)

    @mock.patch('pypowervm.adapter.Session')
    @mock.patch('pypowervm.adapter.Adapter')
    def test_list_client_adpts(self, fake_adapter, fake_session):
        '''
        Validates that the CNA's can be iterated against.
        '''
        test_utils = self.__build_fake_utils(fake_adapter, fake_session,
                self.cna_resp)

        # Override the VM Entries with a fake CNA
        class FakeVM(object):
            def get_cna_uris(self):
                return ['mocked']
        vm = FakeVM()

        def list_vms():
            return [vm]

        test_utils._list_vm_entries = list_vms

        # Get the CNAs and validate
        cnas = test_utils.list_client_adpts()
        self.assertEqual(1, len(cnas))
