# Copyright 2014, 2016 IBM Corp.
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

import fixtures
import mock

from networking_powervm.plugins.ibm.agent.powervm import sea_agent
from networking_powervm.tests.unit.plugins.ibm.powervm import base

from neutron_lib import constants as q_const
from oslo_config import cfg
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import network as pvm_net
from pypowervm.wrappers import virtual_io_server as pvm_vios


def fake_nb(uuid, pvid, tagged_vlans, addl_vlans):
    return mock.MagicMock(
        uuid=uuid,
        load_grps=[mock.MagicMock(pvid=pvid, tagged_vlans=tagged_vlans)],
        list_vlans=mock.Mock(return_value=[pvid] + tagged_vlans + addl_vlans))


class FakeException(Exception):
    """Used to indicate an error in an API the agent calls."""
    pass


class SEAAgentTest(base.BasePVMTestCase):

    def setUp(self):
        super(SEAAgentTest, self).setUp()

        self.agtfx = self.useFixture(base.AgentFx())

        # Mock the mgmt uuid
        self.useFixture(fixtures.MockPatch(
            'pypowervm.tasks.partition.get_mgmt_partition')
        ).mock.return_value = mock.MagicMock(uuid='mgmt_uuid')

        self.mock_parse_sea_mappings = self.useFixture(fixtures.MockPatch(
            'networking_powervm.plugins.ibm.agent.powervm.utils.'
            'parse_sea_mappings')).mock
        self.mock_parse_sea_mappings.return_value = {'default': 'nb_uuid'}

        cfg.CONF.set_override('bridge_mappings', 'the_bridge_maps',
                              group='AGENT')

        self.agent = sea_agent.SharedEthernetNeutronAgent()

    def test_init(self):
        """Verifies the integrity of the agent after being initialized."""
        self.assertEqual('networking-powervm-sharedethernet-agent',
                         self.agent.agent_state.get('binary'))
        self.assertEqual(q_const.L2_AGENT_TOPIC,
                         self.agent.agent_state.get('topic'))
        self.mock_parse_sea_mappings.assert_called_once_with(
            self.agent.adapter, self.agent.host_uuid, 'the_bridge_maps')
        self.assertEqual(
            {'default': 'nb_uuid'},
            self.agent.agent_state['configurations']['bridge_mappings'])
        self.assertEqual('PowerVM Shared Ethernet agent',
                         self.agent.agent_state.get('agent_type'))
        self.assertEqual(True, self.agent.agent_state.get('start_flag'))
        # Other @propertys
        self.assertEqual('sea-agent-%s' % cfg.CONF.host, self.agent.agent_id)
        self.assertEqual(pvm_net.CNA, self.agent.vif_wrapper_class)

    @mock.patch('pypowervm.tasks.network_bridger.ensure_vlans_on_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_provision_devices(self, mock_base_prov, mock_ensure):
        """Validates that the provision is invoked with batched VLANs."""
        preq1 = base.mk_preq('plug', 'aa', segment_id=20,
                             phys_network='default', vif_type='pvm_sea')
        preq2 = base.mk_preq('plug', 'bb', segment_id=22,
                             phys_network='default', vif_type='pvm_sea')
        preq3 = base.mk_preq('unplug', 'cc', segment_id=24,
                             phys_network='default', vif_type='pvm_sea')
        # Invoke
        self.agent.provision_devices({preq1, preq2, preq3})

        # Validate that both VLANs are in one call
        mock_ensure.assert_called_once_with(
            self.agent.adapter, self.agent.host_uuid, 'nb_uuid', {20, 22})

        # Validate that the devices were marked up
        mock_base_prov.assert_called_once_with({preq1, preq2})

        # Validate the behavior of a failed VLAN provision.
        mock_ensure.reset_mock()
        mock_base_prov.reset_mock()
        # Have the ensure throw some exception
        mock_ensure.side_effect = FakeException()

        # Invoke
        self.assertRaises(FakeException, self.agent.provision_devices,
                          {preq1, preq2, preq3})

        # Validate that both VLANs are in one call.  Should still occur even
        # though no exception.
        mock_ensure.assert_called_once_with(
            self.agent.adapter, self.agent.host_uuid, 'nb_uuid', {20, 22})

        # However, the port update should not be invoked.
        mock_base_prov.assert_not_called()

    @mock.patch('pypowervm.tasks.network_bridger.remove_vlan_from_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sea_agent.'
                'SharedEthernetNeutronAgent._get_nb_and_vlan')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sea_agent.'
                'SharedEthernetNeutronAgent.provision_devices')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'get_vswitch_map')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_vifs')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'find_nb_for_cna')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.prov_req.'
                'ProvisionRequest.for_wrappers')
    def test_heal_and_optimize(
            self, mock_preq, mock_find_nb_for_cna, mock_list_bridges,
            mock_list_cnas, mock_vs_map, mock_prov_devs, mock_get_nb_and_vlan,
            mock_nbr_remove):
        """Validates the heal and optimization code.  Limited to 3 deletes."""
        # Fake adapters already on system.
        mgmt_lpar = mock.Mock(spec=pvm_lpar.LPAR, is_mgmt_partition=True)
        reg_lpar = mock.Mock(spec=pvm_lpar.LPAR, is_mgmt_partition=False)
        mgmt_vios = mock.Mock(spec=pvm_vios.VIOS, is_mgmt_partition=True)
        reg_vios = mock.Mock(spec=pvm_vios.VIOS, is_mgmt_partition=False)
        cna1 = mock.MagicMock(mac='00', pvid=30, tagged_vlans=[])
        cna2 = mock.MagicMock(mac='11', pvid=31, tagged_vlans=[32, 33, 34])
        mock_list_cnas.return_value = {
            mgmt_lpar: [cna1], reg_lpar: [cna2], mgmt_vios: [], reg_vios: []}

        # The neutron data.  These will be 'ensured' on the bridge.
        preq1 = base.mk_preq('plug', '00', segment_id=20,
                             phys_network='default')
        preq2 = base.mk_preq('plug', '22', segment_id=22,
                             phys_network='default')
        preq3 = base.mk_preq('unplug', '55', segment_id=55,
                             phys_network='default')
        mock_preq.return_value = [preq1, preq2, preq3]

        # Mock a provision request
        mock_get_nb_and_vlan.return_value = ('nb2_uuid', 23)

        # Mock up network bridges.  VLANs 44, 45, and 46 should be deleted
        # as they are not required by anything.  VLAN 47 should be needed
        # as it is in the pending list.  VLAN 48 should be deleted, but will
        # put over the three delete max count (and therefore would be hit in
        # next pass)
        mock_nb1 = fake_nb('nb_uuid', 20, [], [])
        mock_nb2 = fake_nb('nb2_uuid', 40, [41, 42, 43], [44, 45, 46, 47, 48])
        mock_list_bridges.return_value = [mock_nb1, mock_nb2]
        mock_find_nb_for_cna.return_value = mock_nb2

        # Invoke
        self.agent.heal_and_optimize()

        mock_list_cnas.assert_called_once_with(self.agent.adapter, pvm_net.CNA,
                                               include_vios_and_mgmt=True)
        # Filtered down to the non-mgmt LPAR
        mock_preq.assert_called_once_with(self.agent, {reg_lpar: [cna2]},
                                          'plug')
        mock_list_bridges.assert_called_once_with(self.agent.adapter,
                                                  self.agent.host_uuid)
        mock_prov_devs.assert_called_with([preq1, preq2, preq3])
        mock_get_nb_and_vlan.assert_has_calls(
            [mock.call(req.rpc_device, emit_warnings=False) for req in
             (preq1, preq2, preq3)])
        mock_vs_map.assert_called_once_with(self.agent.adapter,
                                            self.agent.host_uuid)
        mock_find_nb_for_cna.assert_has_calls(
            [mock.call(mock_list_bridges.return_value, cna,
                       mock_vs_map.return_value) for cna in (cna1, cna2)],
            any_order=True)

        # One remove call per net bridge.
        mock_nbr_remove.assert_has_calls(
            [mock.call(
                self.agent.adapter, self.agent.host_uuid, 'nb2_uuid', vlan)
             for vlan in (44, 45, 48)], any_order=True)

        # Validate no remove.
        mock_nbr_remove.reset_mock()
        mock_prov_devs.reset_mock()
        # Set that we can't do the clean up
        cfg.CONF.set_override('automated_powervm_vlan_cleanup', False,
                              group='AGENT')

        # Invoke
        self.agent.heal_and_optimize()

        # Verify.  One ensure call per net bridge.  Zero for the remove as that
        # has been flagged to not clean up.
        mock_nbr_remove.assert_not_called()
        mock_prov_devs.assert_called_with([preq1, preq2, preq3])

    def test_get_nb_and_vlan(self):
        """Be sure nb uuid and vlan parsed from dev properly."""
        self.assertEqual(('nb_uuid', 100), self.agent._get_nb_and_vlan(
            {'physical_network': 'default', 'segmentation_id': 100}))
