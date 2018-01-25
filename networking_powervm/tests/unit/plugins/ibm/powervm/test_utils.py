# Copyright 2014, 2017 IBM Corp.
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

from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import managed_system as pvm_ms
from pypowervm.wrappers import virtual_io_server as pvm_vios


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

    def test_norm_mac(self):
        EXPECTED = "12:34:56:78:90:ab"
        self.assertEqual(EXPECTED, utils.norm_mac("12:34:56:78:90:ab"))
        self.assertEqual(EXPECTED, utils.norm_mac("1234567890ab"))
        self.assertEqual(EXPECTED, utils.norm_mac("12:34:56:78:90:AB"))
        self.assertEqual(EXPECTED, utils.norm_mac("1234567890AB"))

    @mock.patch('pypowervm.wrappers.network.NetBridge.get')
    def test_list_bridges(self, mock_nbrget):
        """Test that we can load the bridges in properly."""
        mock_nbrget.return_value = ['br1', 'br2']

        # Assert that two are read in
        bridges = utils.list_bridges('adpt', 'host_uuid')
        self.assertEqual(2, len(bridges))
        mock_nbrget.assert_called_once_with(
            'adpt', parent_type=pvm_ms.System, parent_uuid='host_uuid')

    @mock.patch('pypowervm.wrappers.network.VSwitch.get')
    def test_get_vswitch_map(self, mock_get):
        # Create mocks
        mock_get.return_value = [
            mock.Mock(related_href='http://9.1.2.3/test', switch_id=0),
            mock.Mock(related_href='http://9.4.5.6/test', switch_id=5)]

        # Run test and verify results
        self.assertEqual({0: 'http://9.1.2.3/test', 5: 'http://9.4.5.6/test'},
                         utils.get_vswitch_map('adpt', 'host_uuid'))
        mock_get.assert_called_once_with(
            'adpt', parent_type=pvm_ms.System, parent_uuid='host_uuid')

    def test_find_nb_for_cna(self):
        # Create mocks
        nb_wrap = mock.Mock()
        nb_wrap.vswitch_id = '0'
        nb_wrap.supports_vlan = mock.Mock(return_value=True)
        nb_wraps = [nb_wrap]

        vswitch_map = {'0': 'http://9.1.2.3/vs1',
                       '1': 'http://9.1.2.3/vs2'}

        mock_client_adpt = mock.MagicMock(vswitch_uri='http://9.1.2.3/vs1')

        # Should have a proper URI, so it should match
        resp = utils.find_nb_for_cna(nb_wraps, mock_client_adpt, vswitch_map)
        self.assertIsNotNone(resp)

        # Should not match if we change the vswitch URI
        mock_client_adpt.vswitch_uri = "Fake"
        resp = utils.find_nb_for_cna(nb_wraps, mock_client_adpt, vswitch_map)
        self.assertIsNone(resp)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_remove_log_helper')
    def test_find_vifs(self, mock_rmlog):
        mock_vif_class = mock.Mock()
        vea1 = mock.Mock(uuid='1', is_tagged_vlan_supported=True, vswitch_id=2)
        vea2 = mock.Mock(uuid='2', is_tagged_vlan_supported=False,
                         spec=['uuid', 'is_tagged_vlan_supported'])
        mock_vif_class.get.return_value = [vea1, vea2]
        lpar = mock.Mock(spec=pvm_lpar.LPAR)

        # Should return both veas, though second has no vswitch id
        # This test is applicable for listing VNIC vifs.
        self.assertEqual([vea1, vea2],
                         utils._find_vifs('adap', mock_vif_class, lpar, [0]))

    @mock.patch('pypowervm.tasks.partition.get_partitions')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_find_vifs')
    @mock.patch('pypowervm.wrappers.managed_system.System.get',
                mock.MagicMock(return_value=['']))
    @mock.patch('pypowervm.wrappers.network.VSwitch.get')
    def test_list_vifs(self, mock_get_vswitches, mock_find_vifs,
                       mock_get_partitions):
        pars = ['par1', 'par2', 'par3']
        mock_get_partitions.return_value = pars
        mock_find_vifs.side_effect = ['vif1', 'vif2', 'vif3']
        vswitch = mock.MagicMock()
        vswitch.name = 'test'
        mock_get_vswitches.return_value = [vswitch]

        # Default (no VIOS/mgmt)
        self.assertEqual({'par1': 'vif1', 'par2': 'vif2', 'par3': 'vif3'},
                         utils.list_vifs('adap', 'vif_class'))
        mock_get_partitions.assert_called_once_with(
            'adap', lpars=True, vioses=False, mgmt=False)
        mock_find_vifs.assert_has_calls(
            [mock.call('adap', 'vif_class', vm_wrap, []) for vm_wrap in pars])

        mock_get_partitions.reset_mock()
        mock_find_vifs.reset_mock()
        mock_find_vifs.side_effect = ['vif1', 'vif2', 'vif3']

        # With VIOS/mgmt
        self.assertEqual({'par1': 'vif1', 'par2': 'vif2', 'par3': 'vif3'},
                         utils.list_vifs('adap', 'vif_class',
                                         include_vios_and_mgmt=True))
        mock_get_partitions.assert_called_once_with(
            'adap', lpars=True, vioses=True, mgmt=True)
        mock_find_vifs.assert_has_calls(
            [mock.call('adap', 'vif_class', vm_wrap, []) for vm_wrap in pars])

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    @mock.patch('pypowervm.wrappers.virtual_io_server.VIOS.get')
    def test_parse_sea_mappings(self, mock_vioget, mock_list_br):
        # Create mocks
        mock_vios = mock.Mock()
        mock_vios.configure_mock(name='21-25D0A',
                                 uuid="4E0B057C-F052-4609-8EDE-071C7FC485BD")

        mock_sea = mock.Mock(
            dev_name='ent8',
            vio_uri='http://localhost:12080/rest/api/uom/ManagedSystem/'
                    'b7f6d2f3-c4f3-33e4-91ea-1aaeada65d7b/VirtualIOServer/'
                    '4E0B057C-F052-4609-8EDE-071C7FC485BD')
        nb_wrap = mock.Mock()
        nb_wrap.seas = [mock_sea]
        nb_wrap.uuid = '764f3423-04c5-3b96-95a3-4764065400bd'
        nb_wraps = [nb_wrap]
        mock_list_br.return_value = nb_wraps
        mock_vioget.return_value = [mock_vios]

        # Run actual test
        resp = utils.parse_sea_mappings('adap', 'host_uuid',
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

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                '_remove_log_helper')
    def test_find_cnas(self, mock_rmlog):
        mock_vif_class = mock.Mock()
        vea1 = mock.Mock(uuid='1', is_tagged_vlan_supported=True, vswitch_id=2)
        vea2 = mock.Mock(uuid='2', is_tagged_vlan_supported=False,
                         vswitch_id=3)
        vea3 = mock.Mock(uuid='3', is_tagged_vlan_supported=True, vswitch_id=4)
        vea4 = mock.Mock(uuid='3', is_tagged_vlan_supported=True, vswitch_id=0)
        mock_vif_class.get.return_value = [vea1, vea2, vea3, vea4]

        lpar = mock.Mock(spec=pvm_lpar.LPAR)
        vios = mock.Mock(spec=pvm_vios.VIOS)

        # The LPAR type should include the trunk adapters
        self.assertEqual([vea1, vea2, vea3],
                         utils._find_vifs('adap', mock_vif_class, lpar, [0]))
        mock_vif_class.get.assert_called_once_with(
            'adap', parent=lpar, helpers=mock_rmlog.return_value)
        mock_rmlog.assert_called_once_with('adap')

        mock_vif_class.get.reset_mock()
        mock_rmlog.reset_mock()

        # The vios type should ignore the trunk adapters
        self.assertEqual([vea2],
                         utils._find_vifs('adap', mock_vif_class, vios, []))
        mock_vif_class.get.assert_called_once_with(
            'adap', parent=vios, helpers=mock_rmlog.return_value)
        mock_rmlog.assert_called_once_with('adap')
