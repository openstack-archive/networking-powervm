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

from oslo_config import cfg
from pypowervm.tests import test_fixtures as pvm_fx

from neutron_powervm.plugins.ibm.agent.powervm import agent_base
from neutron_powervm.tests.unit.plugins.ibm.powervm import base


class FakeExc(Exception):
    pass


def FakeNPort(mac, segment_id, phys_network):
    return {'mac_address': mac, 'segmentation_id': segment_id,
            'physical_network': phys_network}


class TestAgentBase(base.BasePVMTestCase):

    def setUp(self):
        super(TestAgentBase, self).setUp()

    def build_test_agent(self):
        """Builds a simple test agent."""
        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        with mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                        'BasePVMNeutronAgent.setup_adapter'),\
                mock.patch('neutron_powervm.plugins.ibm.agent.powervm.'
                           'agent_base.BasePVMNeutronAgent.setup_rpc'):
            agent = agent_base.BasePVMNeutronAgent('binary_name', 'agent_type')
            agent.context = mock.Mock()
            agent.agent_id = 'pvm'
            agent.plugin_rpc = mock.MagicMock()
            agent.adapter = self.adpt
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
                'build_prov_requests')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision(self, mock_provision,
                               mock_build_prov_requests):
        """Tests a successful 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs
        mock_build_prov_requests.return_value = devs

        # Invoke the test method.
        agent.attempt_provision([FakeNPort('a', 1, 'default'),
                                 FakeNPort('b', 1, 'default'),
                                 FakeNPort('c', 1, 'default')])

        # Validate the provision was invoked.
        mock_provision.assert_called_with(devs)
        for net_dev in devs:
            self.assertFalse(net_dev.called)

    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'build_prov_requests')
    @mock.patch('neutron_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision_failure(self, mock_provision,
                                       mock_build_prov_requests):
        """Tests a failed 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        # Trigger some failure
        mock_provision.side_effect = FakeExc()
        net_devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        mock_build_prov_requests.return_value = net_devs

        # Invoke the test method.
        self.assertRaises(FakeExc, agent.attempt_provision,
                          [FakeNPort('a', 1, 'default'),
                           FakeNPort('b', 1, 'default'),
                           FakeNPort('c', 1, 'default')])

        # Validate the provision was invoked, but failed.
        mock_provision.assert_called_with(net_devs)
        for net_dev in net_devs:
            self.assertTrue(net_dev.mark_down.called)

    def test_build_prov_requests(self):
        # Do a base check
        self.assertEqual([], agent_base.build_prov_requests([], []))

        cfg.CONF.set_override('host', 'fake_host')

        def build_port(pid, use_good_host=True):
            if use_good_host:
                return {'id': pid, 'binding:host_id': 'fake_host'}
            else:
                return {'id': pid, 'binding:host_id': 'bad_fake_host'}

        # Only 2 should be created
        ports = [build_port('1'), {}, build_port('2'),
                 build_port('4', use_good_host=False)]
        devs = [{'port_id': '3'}, {}, {'port_id': '1'}, {'port_id': '2'}]
        resp = agent_base.build_prov_requests(devs, ports)
        self.assertEqual(2, len(resp))
