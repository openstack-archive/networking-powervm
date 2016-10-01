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
import time

import mock

from networking_powervm.plugins.ibm.agent.powervm import sriov_agent
from networking_powervm.tests.unit.plugins.ibm.powervm import base
from pypowervm.tests import test_fixtures as pvm_fx
from pypowervm.wrappers import logical_partition as pvm_lpar


class SRIOVAgentTest(base.BasePVMTestCase):
    def setUp(self):
        super(SRIOVAgentTest, self).setUp()
        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        host_uuid_p = mock.patch('networking_powervm.plugins.ibm.agent.'
                                 'powervm.utils.get_host_uuid')
        self.mock_host_uuid = host_uuid_p.start()
        self.addCleanup(host_uuid_p.stop)

    def test_port_timeout(self):
        # This test had better take less than 19 minutes to run
        now = time.time()
        port = {'update_received_at': now - 60}
        self.assertFalse(sriov_agent.port_timed_out(port))
        port = {'update_received_at': now - (21 * 60)}
        self.assertTrue(sriov_agent.port_timed_out(port))

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
        self.assertIsNone(
            agt.agent_state['configurations']['default_capacity'])
        # Validate parse_bridge_mappings
        br_map = agt.br_map
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
                'SRIOVNeutronAgent.parse_bridge_mappings', new=mock.Mock())
    @mock.patch('pypowervm.util.sanitize_mac_for_api')
    @mock.patch('pypowervm.wrappers.iocard.VNIC.search')
    def test_is_vif_plugged(self, mock_srch, mock_san):
        agt = sriov_agent.SRIOVNeutronAgent()
        port = {'mac_address': 'big_mac'}
        self.assertEqual(mock_srch.return_value, agt.is_vif_plugged(port))
        mock_san.assert_called_once_with('big_mac')
        mock_srch.assert_called_once_with(
            agt.adapter, parent_type=pvm_lpar.LPAR, mac=mock_san.return_value)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent.parse_bridge_mappings')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'port_timed_out')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sriov_agent.'
                'SRIOVNeutronAgent.is_vif_plugged')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_up')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.get_device_details')
    @mock.patch('time.sleep')
    def test_rpc_loop(self, mock_slp, mock_gdd, mock_udu, mock_ivp, mock_pto,
                      mock_pbm):
        # Also verifies _update_port
        agt = sriov_agent.SRIOVNeutronAgent()
        # Ignore parse_bridge_mappings() calls from SRIOVNeutronAgent init.
        mock_pbm.reset_mock()
        port1 = {'mac_address': 'mac1'}
        port2 = {'mac_address': 'mac2'}
        # Limit to three outer loops.  But let me call it once to set up.
        sleepiter = iter((True, True, True, False))

        def validate_sleep(interval):
            # sleep called with overridden polling_interval
            self.assertEqual(5, interval)
            if next(sleepiter):
                # Re-populate the queue after each inner iteration so we can
                # hit the outer loop multiple times.
                agt._update_port(port1)
                agt._update_port(port2)
            else:
                # Use a weird exception so we know it's us
                raise KeyboardInterrupt
        mock_slp.side_effect = validate_sleep
        # Pretend the ports have timed out once in the middle
        mock_pto.side_effect = ([False] * 6) + [True, True] + ([False] * 4)

        # Prepopulate the queue with ports to process
        validate_sleep(5)
        # VIF is plugged the second time around, for each vif, on each loop
        # (except when we time out, where is_vif_plugged isn't reached).
        # VIFs get requeued to the end.
        mock_ivp.side_effect = [False, False, True, True] * 4
        # Override the polling interval to make sure it comes through
        cfg.CONF.set_override('polling_interval', 5, group='AGENT')
        # Invoke the loop
        self.assertRaises(KeyboardInterrupt, agt.rpc_loop)
        # parse_bridge_mappings called thrice with no args
        self.assertEqual(3, mock_pbm.call_count)
        mock_pbm.assert_has_calls([mock.call()] * 3)
        self.assertEqual(mock_pbm.return_value,
                         agt.agent_state['configurations']['bridge_mappings'])
        # is_vif_plugged called twice per iteration per port (except for when
        # the port timed out).  The first time for each vif it returns False,
        # so that port gets requeued at the end.
        self.assertEqual(8, mock_ivp.call_count)
        mock_ivp.assert_has_calls(
            [mock.call(port) for port in (port1, port2)] * 4)
        # update_device_up & get_device_details called once per port, per outer
        # iteration, except when we timed out.
        self.assertEqual(4, mock_udu.call_count)
        mock_udu.assert_has_calls([mock.call(mock_gdd.return_value)] * 4)
        self.assertEqual(4, mock_gdd.call_count)
        mock_gdd.assert_has_calls([mock.call('mac1'), mock.call('mac2')] * 2)
