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

from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.tests.unit.plugins.ibm.powervm import base


class FakeExc(Exception):
    pass


def FakeNPort(mac, segment_id, phys_network):
    return {'mac_address': mac, 'segmentation_id': segment_id,
            'physical_network': phys_network}


class TestAgentBase(base.BasePVMTestCase):

    def build_test_agent(self):
        """Builds a simple test agent."""
        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        with mock.patch('networking_powervm.plugins.ibm.agent.powervm.'
                        'agent_base.BasePVMNeutronAgent.setup_adapter'),\
                mock.patch('networking_powervm.plugins.ibm.agent.powervm.'
                           'agent_base.BasePVMNeutronAgent.setup_rpc'):
            agent = agent_base.BasePVMNeutronAgent('binary_name', 'agent_type')
            agent.context = mock.Mock()
            agent.agent_id = 'pvm'
            agent.plugin_rpc = mock.MagicMock()
            agent.adapter = self.adpt
        return agent

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision(self, mock_provision):
        """Tests a successful 'attempt_provision' invocation."""
        agent = self.build_test_agent()
        provision_reqs = [mock.Mock(rpc_device='a', mac_address='a'),
                          mock.Mock(rpc_device='b', mac_address='b'),
                          mock.Mock(rpc_device='c', mac_address='c')]

        # Invoke the test method.
        agent.attempt_provision(provision_reqs)

        # Validate the provision was invoked.
        mock_provision.assert_called_with(provision_reqs)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_down')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision_failure(self, mock_provision, mock_dev_down):
        """Tests a failed 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        # Trigger some failure
        mock_provision.side_effect = FakeExc()
        provision_reqs = [mock.Mock(rpc_device='a', mac_address='a'),
                          mock.Mock(rpc_device='b', mac_address='b'),
                          mock.Mock(rpc_device='c', mac_address='c')]

        # Invoke the test method.
        self.assertRaises(FakeExc, agent.attempt_provision, provision_reqs)

        # Validate the provision was invoked, but failed.
        mock_provision.assert_called_with(provision_reqs)
        self.assertEqual(3, mock_dev_down.call_count)

    @mock.patch('pypowervm.utils.uuid.convert_uuid_to_pvm')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent._list_updated_ports')
    def test_build_prov_requests_from_neutron(self, mock_list_uports,
                                              mock_pvid_convert):
        # Do a base check
        agent = self.build_test_agent()
        mock_list_uports.return_value = []
        self.assertEqual([], agent.build_prov_requests_from_neutron())

        cfg.CONF.set_override('host', 'fake_host')

        def build_port(pid, use_good_host=True):
            if use_good_host:
                return {'id': pid, 'binding:host_id': 'fake_host'}
            else:
                return {'id': pid, 'binding:host_id': 'bad_fake_host'}

        # Only 2 should be created
        mock_list_uports.return_value = [build_port('1'), {}, build_port('2'),
                                         build_port('4', use_good_host=False)]
        devs = [{'port_id': '3'}, {}, {'port_id': '1'}, {'port_id': '2'}]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        resp = agent.build_prov_requests_from_neutron()
        self.assertEqual(2, len(resp))


class TestProvisionRequest(base.BasePVMTestCase):

    def build_dev(self, segmentation_id, mac):
        return {'segmentation_id': segmentation_id, 'mac_address': mac,
                'physical_network': 'default', 'device_owner': 'nova:compute'}

    def build_preq(self, segmentation_id, mac):
        return agent_base.ProvisionRequest(
            self.build_dev(segmentation_id, mac), '1')

    def test_duplicate_removal(self):
        reqs = [self.build_preq(1, 'a'), self.build_preq(1, 'a'),
                self.build_preq(1, 'b'), self.build_preq(1, 'b'),
                self.build_preq(2, 'c'), self.build_preq(3, 'd')]
        reqs = list(set(reqs))

        # Make sure that the list is properly reduced.
        self.assertEqual(4, len(reqs))
        expected = [self.build_preq(1, 'a'), self.build_preq(1, 'b'),
                    self.build_preq(2, 'c'), self.build_preq(3, 'd')]
        for needle in expected:
            self.assertIn(needle, reqs)
