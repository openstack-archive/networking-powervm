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

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.utils.'
                'PVMUtils')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.heal_and_optimize')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_ports')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent._list_updated_ports')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.setup_rpc')
    def test_rpc_loop(self, mock_rpc, mock_list_ports, mock_provision_ports,
                      mock_heal, mock_pvm_utils):
        self.agent = agent_base.BasePVMNeutronAgent('binary_name',
                                                    'agent_type')

        mock_list_ports.side_effect = [['a'], ['b', 'c'], ['d'], ['e'],
                                       ['f'], ['g'], ['h']]
        mock_provision_ports.side_effect = [None, FakeExc(), FakeExc(),
                                            None, FakeExc(), FakeExc(),
                                            FakeExc()]

        # Call the loop.  The last three failures should be where it dies out.
        self.assertRaises(FakeExc, self.agent.rpc_loop)

        # 7 calls total.
        self.assertEqual(7, mock_provision_ports.call_count)
