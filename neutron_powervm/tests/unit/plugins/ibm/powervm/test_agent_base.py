# Copyright 2015 IBM Corp.
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

from neutron_powervm.plugins.ibm.agent.powervm import agent_base
from neutron_powervm.tests.unit.plugins.ibm.powervm import base


class FakeExc(Exception):
    pass


class TestAgentBase(base.BasePVMTestCase):

    def setUp(self):
        super(TestAgentBase, self).setUp()

    def build_test_agent(self):
        """Builds a simple test agent."""
        with mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                        'PVMUtils'),\
                mock.patch('neutron_powervm.plugins.ibm.agent.powervm.'
                           'agent_base.BasePVMNeutronAgent.setup_rpc'):
            agent = agent_base.BasePVMNeutronAgent('binary_name', 'agent_type')
            agent.context = mock.Mock()
            agent.agent_id = 'pvm'
            agent.plugin_rpc = mock.MagicMock()
        return agent

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.heal_and_optimize')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.attempt_provision')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent._list_updated_ports')
    def test_rpc_loop(self, mock_list_ports, mock_provision, mock_heal):
        agent = self.build_test_agent()

        mock_list_ports.side_effect = [['a'], ['b', 'c'], ['d'], ['e'],
                                       ['f'], ['g'], ['h']]
        mock_provision.side_effect = [None, FakeExc(), FakeExc(), None,
                                      FakeExc(), FakeExc(), FakeExc()]

        # Call the loop.  The last three failures should be where it dies out.
        self.assertRaises(FakeExc, agent.rpc_loop)

        # 7 calls total.
        self.assertEqual(7, mock_provision.call_count)

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_down')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision(self, mock_provision, mock_dev_down):
        """Tests a successful 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        # Invoke the test method.
        agent.attempt_provision([mock.MagicMock(), mock.MagicMock(),
                                 mock.MagicMock()])

        # Validate the provision was invoked.
        mock_provision.assert_called_with(devs)
        self.assertEqual(0, mock_dev_down.call_count)

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_down')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision_failure(self, mock_provision, mock_dev_down):
        """Tests a failed 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        # Trigger some failure
        mock_provision.side_effect = FakeExc()

        # Invoke the test method.
        self.assertRaises(FakeExc, agent.attempt_provision,
                          [mock.MagicMock(), mock.MagicMock(),
                           mock.MagicMock()])

        # Validate the provision was invoked.
        mock_provision.assert_called_with(devs)
        self.assertEqual(3, mock_dev_down.call_count)
