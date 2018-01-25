# Copyright 2016, 2017 IBM Corp.
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

from oslo_config import cfg
from pypowervm.wrappers import iocard as pvm_card

import mock

from networking_powervm.plugins.ibm.agent.powervm import sriov_agent
from networking_powervm.tests.unit.plugins.ibm.powervm import base


class SRIOVAgentTest(base.BasePVMTestCase):
    def setUp(self):
        super(SRIOVAgentTest, self).setUp()
        self.agtfx = self.useFixture(base.AgentFx())
        sriov_adaps = [
            mock.Mock(phys_ports=[
                mock.Mock(loc_code='loc1', label='foo'),
                mock.Mock(loc_code='loc2', label='')]),
            mock.Mock(phys_ports=[
                mock.Mock(loc_code='loc3', label='bar'),
                mock.Mock(loc_code='loc4', label='foo')])]
        self.agtfx.sys = mock.Mock(
            asio_config=mock.Mock(sriov_adapters=sriov_adaps))
        self.agtfx.sysget.return_value = [self.agtfx.sys]
        self.agtfx.sys.refresh.return_value = self.agtfx.sys
        self.agt = sriov_agent.SRIOVNeutronAgent()

    def test_init(self):
        """Test __init__, customize_agent_state, and parse_bridge_mappings."""
        # SR-IOV-specific agent state attrs were set
        self.assertEqual(
            'networking-powervm-sriov-agent', self.agt.agent_state['binary'])
        self.assertEqual('PowerVM SR-IOV Ethernet agent',
                         self.agt.agent_state['agent_type'])
        # Validate customize_agent_state
        self.assertEqual(
            2, self.agt.agent_state['configurations']['default_redundancy'])
        self.assertIsNone(
            self.agt.agent_state['configurations']['default_capacity'])
        # Validate parse_bridge_mappings
        self.agtfx.sys.refresh.assert_called_once_with()
        br_map = self.agt.br_map
        self.assertEqual({'default', 'foo', 'bar'}, set(br_map.keys()))
        self.assertEqual(['loc2'], br_map['default'])
        self.assertEqual(['loc3'], br_map['bar'])
        self.assertEqual({'loc1', 'loc4'}, set(br_map['foo']))

        # Ensure conf.vnic_required_vfs affects default_redundancy & _capacity
        cfg.CONF.set_override('vnic_required_vfs', 3, group='AGENT')
        cfg.CONF.set_override('vnic_vf_capacity', 0.06, group='AGENT')
        agt = sriov_agent.SRIOVNeutronAgent()
        self.assertEqual(
            3, agt.agent_state['configurations']['default_redundancy'])
        self.assertEqual(
            0.06, agt.agent_state['configurations']['default_capacity'])

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent._refresh_bridge_mappings_to_neutron')
    def test_port_update(self, mock_refresh):
        """Test the port_update override."""
        self.agt.port_update('context')
        mock_refresh.assert_called_once_with()

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent.parse_bridge_mappings')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent._report_state')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_vifs')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.prov_req.'
                'ProvisionRequest.for_wrappers')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_heal_and_optimize(self, mock_prv, mock_preq, mock_vifs, mock_rpt,
                               mock_pbm):
        """Test heal_and_optimize and _refresh_bridge_mappings_to_neutron."""
        self.agt.heal_and_optimize()
        mock_pbm.assert_called_once_with()
        mock_rpt.assert_called_once_with()
        self.assertEqual(
            mock_pbm.return_value,
            self.agt.agent_state['configurations']['bridge_mappings'])
        mock_vifs.assert_called_once_with(self.agt.adapter, pvm_card.VNIC)
        mock_preq.assert_called_once_with(self.agt, mock_vifs.return_value,
                                          'plug')
        mock_prv.assert_called_once_with(mock_preq.return_value)

    def test_is_hao_event(self):
        self.assertFalse(self.agt.is_hao_event(mock.Mock(detail=None)))
        self.assertFalse(self.agt.is_hao_event(mock.Mock(detail='')))
        self.assertFalse(self.agt.is_hao_event(mock.Mock(detail='Foo')))
        self.assertFalse(self.agt.is_hao_event(mock.Mock(detail='Foo,Bar')))
        self.assertTrue(self.agt.is_hao_event(mock.Mock(
            detail='SRIOVPhysicalPort.ConfigChange')))
        self.assertTrue(self.agt.is_hao_event(mock.Mock(
            detail='Foo,SRIOVPhysicalPort.ConfigChange')))
        self.assertTrue(self.agt.is_hao_event(mock.Mock(
            detail='SRIOVPhysicalPort.ConfigChange,Bar')))
        self.assertTrue(self.agt.is_hao_event(mock.Mock(
            detail='Foo,SRIOVPhysicalPort.ConfigChange,Bar')))
