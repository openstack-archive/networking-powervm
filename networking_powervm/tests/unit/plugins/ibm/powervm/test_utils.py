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

from networking_powervm.plugins.ibm.agent.powervm import exceptions as np_exc
from networking_powervm.plugins.ibm.agent.powervm import utils
from networking_powervm.tests.unit.plugins.ibm.powervm import base

from pypowervm import const as pvm_const
from pypowervm import exceptions as pvm_exc


class UtilsTest(base.BasePVMTestCase):
    """Tests the utility functions for the Shared Ethernet Adapter Logic."""

    def __cna(self, mac):
        """Create a Client Network Adapter mock."""

        class FakeCNA(object):

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

    def test_find_cna_for_mac(self):
        cna1 = self.__cna("1234567890AB")
        cna2 = self.__cna("123456789012")

        self.assertEqual(cna1, utils.find_cna_for_mac("1234567890AB",
                                                      [cna1, cna2]))
        self.assertEqual(None, utils.find_cna_for_mac("9876543210AB",
                                                      [cna1, cna2]))

    def test_norm_mac(self):
        EXPECTED = "12:34:56:78:90:ab"
        self.assertEqual(EXPECTED, utils.norm_mac("12:34:56:78:90:ab"))
        self.assertEqual(EXPECTED, utils.norm_mac("1234567890ab"))
        self.assertEqual(EXPECTED, utils.norm_mac("12:34:56:78:90:AB"))
        self.assertEqual(EXPECTED, utils.norm_mac("1234567890AB"))

    @mock.patch('pypowervm.wrappers.network.NetBridge.wrap')
    def test_list_bridges(self, mock_wrap):
        """Test that we can load the bridges in properly."""
        mock_wrap.return_value = ['br1', 'br2']
        mock_adpt = mock.Mock()
        mock_adpt.read = mock.Mock()

        # Assert that two are read in
        bridges = utils.list_bridges(mock_adpt, 'host_uuid')
        self.assertEqual(2, len(bridges))

    @mock.patch('pypowervm.wrappers.logical_partition.LPAR.wrap')
    def test_list_vm_entries(self, mock_wrap):
        """Validates that VMs can be iterated on properly."""
        feed = mock.Mock(object)
        feed.entries = ['1', '2', '3']
        vm_feed = mock.Mock(object)
        vm_feed.feed = feed
        adpt = mock.Mock()
        adpt.read = mock.Mock(return_value=vm_feed)

        # Mock the pypowervm wrapper to just return what it's passed
        mock_wrap.side_effect = lambda arg: arg

        # List the VMs and make some assertions
        vm_list = utils._list_vm_entries(adpt, 'host_uuid')
        self.assertEqual(3, len(vm_list))
        for vm in vm_list:
            self.assertIsNotNone(vm)
        self.assertEqual(3, mock_wrap.call_count)

    @mock.patch('pypowervm.wrappers.network.VSwitch.wrap')
    def test_get_vswitch_map(self, mock_wrap):
        # Create mocks
        mock_adpt = mock.Mock()
        mock_adpt.read = mock.Mock()
        vswitch = mock.Mock()
        vswitch.related_href = 'http://9.1.2.3/test'
        vswitch.switch_id = 0
        mock_wrap.return_value = [vswitch]

        # Run test and verify results
        resp = utils.get_vswitch_map(mock_adpt, 'host_uuid')
        self.assertEqual('http://9.1.2.3/test', resp[0])
        mock_adpt.read.assert_called_once_with('ManagedSystem',
                                               child_type='VirtualSwitch',
                                               root_id='host_uuid')

    def test_find_nb_for_cna(self):
        # Create mocks
        nb_wrap = mock.Mock()
        nb_wrap.vswitch_id = '0'
        nb_wrap.supports_vlan = mock.Mock(return_value=True)
        nb_wraps = [nb_wrap]

        vswitch_map = {'0': 'http://9.1.2.3/vs1',
                       '1': 'http://9.1.2.3/vs2'}

        mock_client_adpt = mock.MagicMock()
        mock_client_adpt.vswitch_uri = ('http://9.1.2.3/vs1')

        # Should have a proper URI, so it should match
        resp = utils.find_nb_for_cna(nb_wraps, mock_client_adpt, vswitch_map)
        self.assertIsNotNone(resp)

        # Should not match if we change the vswitch URI
        mock_client_adpt.vswitch_uri = "Fake"
        resp = utils.find_nb_for_cna(nb_wraps, mock_client_adpt, vswitch_map)
        self.assertIsNone(resp)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_list_vm_entries')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_find_cnas')
    @mock.patch('pypowervm.wrappers.network.CNA.wrap')
    def test_list_cnas(self, mock_cna_wrap, mock_find_cnas, mock_list_vms):
        """Validates that the CNA's can be iterated against."""

        # Override the VM Entries with a fake CNA
        class FakeVM(object):
            @property
            def uuid(self):
                return 'fake_uuid'
        vm = FakeVM()

        def list_vms(adapter, host_uuid):
            return [vm]

        mock_find_cnas.return_value = [1]
        mock_list_vms.side_effect = list_vms
        mock_cna_wrap.return_value = ['mocked']

        # Get the CNAs and validate
        cnas = utils.list_cnas(None, 'host_uuid')
        self.assertEqual(1, len(cnas))

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    @mock.patch('pypowervm.wrappers.virtual_io_server.VIOS.wrap')
    def test_parse_sea_mappings(self, mock_wrap, mock_list_br):
        # Create mocks
        class FakeVIOS(object):
            @property
            def name(self):
                return '21-25D0A'

            @property
            def related_href(self):
                return 'https://9.1.2.3/vios1'
        mock_sea = mock.Mock()
        mock_sea.dev_name = 'ent8'
        mock_sea.vio_uri = 'https://9.1.2.3/vios1'
        nb_wrap = mock.Mock()
        nb_wrap.seas = [mock_sea]
        nb_wrap.uuid = '764f3423-04c5-3b96-95a3-4764065400bd'
        nb_wraps = [nb_wrap]
        mock_list_br.return_value = nb_wraps
        mock_adpt = mock.MagicMock()
        mock_wrap.return_value = [FakeVIOS()]

        # Run actual test
        resp = utils.parse_sea_mappings(mock_adpt, 'host_uuid',
                                        'default:ent8:21-25D0A')

        # Verify results
        self.assertEqual(1, len(resp.keys()))
        self.assertIn('default', resp)
        self.assertEqual('764f3423-04c5-3b96-95a3-4764065400bd',
                         resp['default'])

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    def test_parse_sea_mappings_no_bridges(self, mock_list_br):
        mock_list_br.return_value = []
        self.assertRaises(np_exc.NoNetworkBridges, utils.parse_sea_mappings,
                          None, 'host_uuid', '1:2:3')

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_parse_empty_bridge_mapping')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    def test_parse_call_to_empty_bridge(self, mock_list_br, mock_empty):
        mock_list_br.return_value = ['br1']

        utils.parse_sea_mappings(None, 'host_uuid', '')

        # Make sure the _parse_empty_bridge_mapping method was called
        self.assertEqual(1, mock_empty.call_count)

    def test_parse_empty_bridge_mappings(self):
        proper_wrap = mock.MagicMock()
        proper_wrap.uuid = '5'
        resp = utils._parse_empty_bridge_mapping([proper_wrap])

        self.assertEqual({'default': '5'}, resp)

        # Try the failure path
        self.assertRaises(np_exc.MultiBridgeNoMapping,
                          utils._parse_empty_bridge_mapping,
                          [proper_wrap, mock.Mock()])

    def test_update_cna_pvid(self):
        """Validates the update_cna_pvid method."""
        def build_mock():
            # Need to rebuild.  Since it returns itself a standard reset will
            # recurse infinitely.
            cna = mock.MagicMock()
            cna.refresh.return_value = cna
            return cna

        # Attempt happy path
        cna = build_mock()
        utils.update_cna_pvid(cna, 5)
        self.assertEqual(5, cna.pvid)
        self.assertEqual(1, cna.update.call_count)

        # Raise an error 3 times and make sure it eventually re-raises the root
        # etag exception
        cna = build_mock()
        err_resp = mock.MagicMock()
        err_resp.status = pvm_const.HTTPStatus.ETAG_MISMATCH
        error = pvm_exc.HttpError(err_resp)

        cna.update.side_effect = [error, error, error]
        self.assertRaises(pvm_exc.HttpError, utils.update_cna_pvid, cna, 5)
        self.assertEqual(3, cna.update.call_count)
        self.assertEqual(2, cna.refresh.call_count)

        # Raise an error 2 times and then eventually works
        cna = build_mock()
        cna.update.side_effect = [error, error, None]
        utils.update_cna_pvid(cna, 5)
        self.assertEqual(3, cna.update.call_count)
        self.assertEqual(2, cna.refresh.call_count)

        # Immediate re-raise of different type of exception
        cna = build_mock()
        err_resp.status = pvm_const.HTTPStatus.UNAUTHORIZED
        cna.update.side_effect = pvm_exc.HttpError(err_resp)

        self.assertRaises(pvm_exc.HttpError, utils.update_cna_pvid, cna, 5)
        self.assertEqual(1, cna.update.call_count)
        self.assertEqual(0, cna.refresh.call_count)
