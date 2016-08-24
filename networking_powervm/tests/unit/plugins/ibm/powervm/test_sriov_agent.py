# Copyright 2016 IBM Corp.
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

import mock

try:
    import queue
except ImportError:
    import Queue as queue

from networking_powervm.plugins.ibm.agent.powervm import sriov_agent
from networking_powervm.tests.unit.plugins.ibm.powervm import base
from pypowervm.tests import test_fixtures as pvm_fx


class SRIOVAgentTest(base.BasePVMTestCase):
    def setUp(self):
        super(SRIOVAgentTest, self).setUp()
        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        host_uuid_p = mock.patch('networking_powervm.plugins.ibm.agent.'
                                 'powervm.utils.get_host_uuid')
        self.mock_host_uuid = host_uuid_p.start()
        self.addCleanup(host_uuid_p.stop)

    @mock.patch('pypowervm.wrappers.managed_system.System.get')
    def test_init(self, mock_sys_get):
        sriov_adaps = [
            mock.Mock(phys_ports=[
                mock.Mock(loc_code='loc1', label='foo'),
                mock.Mock(loc_code='loc2', label='')]),
            mock.Mock(phys_ports=[
                mock.Mock(loc_code='loc3', label='bar'),
                mock.Mock(loc_code='loc4', label='foo')])]
        mock_sys = mock.Mock(asio_config=mock.Mock(sriov_adapters=sriov_adaps))
        mock_sys_get.return_value = [mock_sys]
        agt = sriov_agent.SRIOVNeutronAgent()
        self.mock_host_uuid.assert_called_once_with(agt.adapter)
        mock_sys_get.assert_called_once_with(agt.adapter)
        # agt._msys got the result from System.get...
        self.assertEqual(mock_sys, agt._msys)
        # ...but invoking the msys @property refreshes the wrapper...
        self.assertEqual(mock_sys.refresh.return_value, agt.msys)
        # ...and doesn't re-get the System
        self.assertEqual(1, mock_sys_get.call_count)
        # SR-IOV-specific agent state attrs were set
        self.assertEqual(
            'networking-powervm-sriov-agent', agt.agent_state['binary'])
        self.assertEqual(
            'PowerVM SR-IOV Ethernet agent', agt.agent_state['agent_type'])
        # Validate customize_agent_state
        self.assertEqual(
            2, agt.agent_state['configurations']['default_redundancy'])
        # Validate parse_bridge_mappings
        br_map = agt.br_map
        self.assertEqual({'default', 'foo', 'bar'}, set(br_map.keys()))
        self.assertEqual(['loc2'], br_map['default'])
        self.assertEqual(['loc3'], br_map['bar'])
        self.assertEqual({'loc1', 'loc4'}, set(br_map['foo']))

        # Ensure conf.vnic_required_vfs affects default_redundancy
        cfg.CONF.set_override('vnic_required_vfs', 3, group='AGENT')
        agt = sriov_agent.SRIOVNeutronAgent()
        self.assertEqual(
            3, agt.agent_state['configurations']['default_redundancy'])

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent.parse_bridge_mappings', new=mock.Mock())
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'queue.Queue.put')
    def test_update_port(self, mock_qput):
        agt = sriov_agent.SRIOVNeutronAgent()
        port = {'mac_address': 5}
        agt._update_port(port)
        mock_qput.assert_called_once_with(port)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent.parse_bridge_mappings')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'queue.Queue.get')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_up')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.get_device_details')
    @mock.patch('time.sleep')
    def test_rpc_loop(self, mock_slp, mock_gdd, mock_udu, mock_qget, mock_pbm):
        agt = sriov_agent.SRIOVNeutronAgent()
        mock_qget.side_effect = [{'mac_address': 'mac1'},
                                 {'mac_address': 'mac2'}, queue.Empty] * 3
        # Limit to three loops.  Use a weird exception so we know we did it.
        mock_slp.side_effect = [None, None, KeyboardInterrupt]
        # Override the polling interval to make sure it comes through
        cfg.CONF.set_override('polling_interval', 5, group='AGENT')
        # Invoke the loop
        self.assertRaises(KeyboardInterrupt, agt.rpc_loop)
        # parse_bridge_mappings called thrice with no args
        mock_pbm.assert_has_calls([mock.call()] * 3)
        self.assertEqual(mock_pbm.return_value,
                         agt.agent_state['configurations']['bridge_mappings'])
        # update_device_up, get_device_details called six times
        mock_udu.assert_has_calls([mock.call(mock_gdd.return_value)] * 6)
        mock_gdd.assert_has_calls([mock.call('mac1'), mock.call('mac2')] * 3)
        # Sleep called thrice with overridden polling_interval
        mock_slp.assert_has_calls([mock.call(5)] * 3)
