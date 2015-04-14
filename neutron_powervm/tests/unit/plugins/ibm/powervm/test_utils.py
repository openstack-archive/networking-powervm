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

from neutron_powervm.tests.unit.plugins.ibm.powervm import base
from neutron_powervm.tests.unit.plugins.ibm.powervm import fixtures

import os

from pypowervm.tests.wrappers.util import pvmhttp
from pypowervm.wrappers import network as pvm_net

NET_BR_FILE = 'fake_network_bridge.txt'
VM_FILE = 'fake_lpar_feed.txt'
CNA_FILE = 'fake_cna.txt'
VSW_FILE = 'fake_virtual_switch.txt'


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
        self.vswitch_resp = resp(VSW_FILE)

    def __build_fake_utils(self, feed):
        '''
        Helper method to make the mock adapter.
        '''
        fake_adapter = self.useFixture(fixtures.PyPowerVM()).adpt

        # Sets the feed to be the response on the adapter for a single read
        fake_adapter.read.return_value = feed
        fake_adapter.read_by_href.return_value = feed
        with mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                        'PVMUtils._get_host_uuid'):
            test_utils = utils.PVMUtils(None, None, None, None)
        test_utils.adapter = fake_adapter
        return test_utils

    def __cna(self, mac):
        '''Create a Client Network Adapter mock.'''

        class FakeCNA():

            @property
            def slot(self):
                return 1

            @property
            def mac(self):
                return mac

            @property
            def pvid(self):
                return 1

        return FakeCNA()

    def test_find_client_adpt_for_mac(self):
        ut = self.__build_fake_utils(None)

        cna1 = self.__cna("1234567890AB")
        cna2 = self.__cna("123456789012")

        self.assertEqual(cna1, ut.find_client_adpt_for_mac("1234567890AB",
                                                           [cna1, cna2]))
        self.assertEqual(None, ut.find_client_adpt_for_mac("9876543210AB",
                                                           [cna1, cna2]))

    def test_norm_mac(self):
        ut = self.__build_fake_utils(None)

        EXPECTED = "12:34:56:78:90:ab"
        self.assertEqual(EXPECTED, ut.norm_mac("12:34:56:78:90:ab"))
        self.assertEqual(EXPECTED, ut.norm_mac("1234567890ab"))
        self.assertEqual(EXPECTED, ut.norm_mac("12:34:56:78:90:AB"))
        self.assertEqual(EXPECTED, ut.norm_mac("1234567890AB"))

    def test_list_bridges(self):
        '''
        Test that we can load the bridges in properly.
        '''
        test_utils = self.__build_fake_utils(self.net_br_resp)

        # Assert that two are read in
        bridges = test_utils.list_bridges()
        self.assertEqual(2, len(bridges))
        self.assertTrue(isinstance(bridges[0], pvm_net.NetBridge))

    def test_list_vm_entries(self):
        '''
        Validates that VMs can be iterated on properly.
        '''
        test_utils = self.__build_fake_utils(self.vm_feed_resp)

        # List the VMs and make some assertions
        vm_list = test_utils._list_vm_entries()
        self.assertEqual(17, len(vm_list))
        for vm in vm_list:
            self.assertIsNotNone(vm.uuid)

    def test_get_vswitch_map(self):
        test_utils = self.__build_fake_utils(self.vswitch_resp)
        resp = test_utils.get_vswitch_map()
        self.assertEqual('https://9.1.2.3:12443/rest/api/uom/ManagedSystem/'
                         'c5d782c7-44e4-3086-ad15-b16fb039d63b/VirtualSwitch/'
                         'e1a852cb-2be5-3a51-9147-43761bc3d720',
                         resp[0])

    def test_find_nb_for_client_adpt(self):
        test_utils = self.__build_fake_utils(self.vswitch_resp)

        nb_wraps = pvm_net.NetBridge.wrap(self.net_br_resp)

        mock_client_adpt = mock.MagicMock()
        mock_client_adpt.vswitch_uri = ('https://9.1.2.3:12443/rest/api/uom/'
                                        'ManagedSystem/'
                                        'c5d782c7-44e4-3086-ad15-b16fb039d63b/'
                                        'VirtualSwitch/'
                                        'e1a852cb-2be5-3a51-9147-43761bc3d720')

        vswitch_map = test_utils.get_vswitch_map()

        # Should have a proper URI, so it should match
        resp = test_utils.find_nb_for_client_adpt(nb_wraps, mock_client_adpt,
                                                  vswitch_map)
        self.assertIsNotNone(resp)

        # Should not match if we change the vswitch URI
        mock_client_adpt.vswitch_uri = "Fake"
        resp = test_utils.find_nb_for_client_adpt(nb_wraps, mock_client_adpt,
                                                  vswitch_map)
        self.assertIsNone(resp)

    @mock.patch('pypowervm.wrappers.network.CNA.wrap')
    def test_list_client_adpts(self, mock_cna_wrap):
        '''
        Validates that the CNA's can be iterated against.
        '''
        test_utils = self.__build_fake_utils(self.cna_resp)

        # Override the VM Entries with a fake CNA
        class FakeVM(object):
            @property
            def uuid(self):
                return 'fake_uuid'
        vm = FakeVM()

        def list_vms():
            return [vm]

        test_utils._list_vm_entries = list_vms
        mock_cna_wrap.return_value = ['mocked']

        # Get the CNAs and validate
        cnas = test_utils.list_client_adpts()
        self.assertEqual(1, len(cnas))
