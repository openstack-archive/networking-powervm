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
#
# @author: Drew Thorstensen, IBM Corp.

from oslo.config import cfg

import mock

from neutron_powervm.plugins.ibm.agent.powervm import powervm_sea_agent
from neutron_powervm.tests.unit.plugins.ibm.powervm import base

from neutron.common import constants as q_const
from neutron import context as ctx


class SimpleTest(base.BasePVMTestCase):

    def setUp(self):
        super(SimpleTest, self).setUp()

        with mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                'NetworkBridgeUtils'):
            self.agent = powervm_sea_agent.SharedEthernetNeutronAgent()

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                'NetworkBridgeUtils')
    def test_init(self, fake_utils):
        '''
        Verifies the integrity of the agent after being initialized.
        '''
        temp_agent = powervm_sea_agent.SharedEthernetNeutronAgent()
        self.assertEqual('neutron-powervm-sharedethernet-agent',
                         temp_agent.agent_state.get('binary'))
        self.assertEqual(q_const.L2_AGENT_TOPIC,
                         temp_agent.agent_state.get('topic'))
        self.assertEqual(True, temp_agent.agent_state.get('start_flag'))
        self.assertEqual('PowerVM Shared Ethernet agent',
                         temp_agent.agent_state.get('agent_type'))

    def test_updated_ports(self):
        '''
        Validates that the updated ports list can be added to and reset
        properly as needed.
        '''
        self.assertEqual(0, len(self.agent._list_updated_ports()))

        self.agent._update_port(1)
        self.agent._update_port(2)

        self.assertEqual(2, len(self.agent._list_updated_ports()))

        # This should now be reset back to zero length
        self.assertEqual(0, len(self.agent._list_updated_ports()))

    def test_report_state(self):
        '''
        Validates that the report state functions properly.
        '''
        # Make sure we had a start flag before the first report
        self.assertIsNotNone(self.agent.agent_state.get('start_flag'))

        # Mock up the state_rpc
        self.agent.state_rpc = mock.Mock()
        self.agent.context = mock.Mock()

        # run the code
        self.agent._report_state()

        # Devices are not set
        configs = self.agent.agent_state.get('configurations')
        self.assertEqual(0, configs['devices'])

        # Make sure we flipped to None after the report.  Also
        # indicates that we hit the last part of the method and didn't
        # fail.
        self.assertIsNone(self.agent.agent_state.get('start_flag'))

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
            'NetworkBridgeUtils')
    def test_scan_port_delta_add(self, net_utils):
        '''
        Validates that scan works for add
        '''
        net_utils.find_client_adpt_for_mac = mock.MagicMock(return_value=None)
        agent = powervm_sea_agent.SharedEthernetNeutronAgent()
        agent.conn_utils = net_utils

        p1 = self.__mock_n_port('aa:bb:cc:dd:ee:ff')
        resp = agent._scan_port_delta([p1])

        self.assertEqual(1, len(resp.get('added')))
        self.assertEqual(0, len(resp.get('updated')))
        self.assertEqual(0, len(resp.get('removed')))

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                'NetworkBridgeUtils')
    def test_scan_port_delta_updated(self, net_utils):
        '''
        Validates that scan works for update
        '''
        net_utils.find_client_adpt_for_mac = mock.MagicMock(
                return_value=object())
        agent = powervm_sea_agent.SharedEthernetNeutronAgent()
        agent.conn_utils = net_utils

        p1 = self.__mock_n_port('aa:bb:cc:dd:ee:ff')
        resp = agent._scan_port_delta([p1])

        self.assertEqual(0, len(resp.get('added')))
        self.assertEqual(1, len(resp.get('updated')))
        self.assertEqual(0, len(resp.get('removed')))

    def __mock_n_port(self, mac):
        '''Builds a fake neutron port with a given mac'''
        return {'mac': mac}

    @mock.patch('neutron.openstack.common.loopingcall.'
                'FixedIntervalLoopingCall')
    @mock.patch.object(ctx, 'get_admin_context_without_session',
                       return_value=mock.Mock())
    def test_setup_rpc(self, admin_ctxi, mock_loopingcall):
        '''
        Validates that the setup_rpc method is properly invoked
        '''
        cfg.CONF.AGENT = mock.Mock()
        cfg.CONF.AGENT.report_interval = 5

        # Derives the instance that will be returned when a new loopingcall
        # is made.  Used for verification
        instance = mock_loopingcall.return_value

        # Run the method to completion
        self.agent.setup_rpc()

        # Make sure that the loopingcall had an interval of 5.
        instance.start.assert_called_with(interval=5)
